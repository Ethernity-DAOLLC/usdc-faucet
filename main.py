from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime
import os
import json
from pathlib import Path
from dotenv import load_dotenv
from psycopg_pool import AsyncConnectionPool
from web3 import Web3
from decimal import Decimal
import asyncio

load_dotenv()
app = FastAPI(title="USDC Faucet Service", version="1.0.0")
CORS_ORIGINS = os.getenv("BACKEND_CORS_ORIGINS", "http://localhost:3000").split(",")
CORS_ORIGINS = [origin.strip() for origin in CORS_ORIGINS if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATABASE_URL = os.getenv("DATABASE_URL")
PRIVATE_KEY = os.getenv("FAUCET_PRIVATE_KEY")
RPC_URL = os.getenv("RPC_URL")
USDC_CONTRACT_ADDRESS = os.getenv("USDC_CONTRACT_ADDRESS")
MOCK_USDC_ADDRESS = os.getenv("MOCK_USDC_ADDRESS")
USE_MOCK_USDC = os.getenv("USE_MOCK_USDC", "false").lower() == "true"
FAUCET_AMOUNT = os.getenv("FAUCET_AMOUNT", "1000")
RATE_LIMIT_HOURS = int(os.getenv("RATE_LIMIT_HOURS", "24"))

if not all([DATABASE_URL, PRIVATE_KEY, RPC_URL]):
    raise ValueError("Missing required environment variables")

if not USE_MOCK_USDC and not USDC_CONTRACT_ADDRESS:
    raise ValueError("USDC_CONTRACT_ADDRESS is required when not using mock")

if USE_MOCK_USDC and not MOCK_USDC_ADDRESS:
    print("⚠️  Warning: USING MOCK USDC without MOCK_USDC_ADDRESS")
w3 = Web3(Web3.HTTPProvider(RPC_URL))

USDC_REAL_ABI = [
    {
        "constant": False,
        "inputs": [
            {"name": "_to", "type": "address"},
            {"name": "_value", "type": "uint256"}
        ],
        "name": "transfer",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [],
        "name": "symbol",
        "outputs": [{"name": "", "type": "string"}],
        "type": "function"
    }
]

MOCK_USDC_ABI = [
    {
        "constant": False,
        "inputs": [
            {"name": "_to", "type": "address"},
            {"name": "_value", "type": "uint256"}
        ],
        "name": "transfer",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [],
        "name": "symbol",
        "outputs": [{"name": "", "type": "string"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [],
        "name": "name",
        "outputs": [{"name": "", "type": "string"}],
        "type": "function"
    },
    {
        "constant": False,
        "inputs": [
            {"name": "_to", "type": "address"},
            {"name": "_amount", "type": "uint256"}
        ],
        "name": "faucet",
        "outputs": [],
        "type": "function"
    },
    {
        "constant": False,
        "inputs": [
            {"name": "_to", "type": "address"},
            {"name": "_amount", "type": "uint256"}
        ],
        "name": "mint",
        "outputs": [],
        "type": "function"
    }
]
if USE_MOCK_USDC and MOCK_USDC_ADDRESS:
    contract_address = MOCK_USDC_ADDRESS
    contract_abi = MOCK_USDC_ABI
    print(f"✅ Using MOCK USDC at: {contract_address}")
else:
    contract_address = USDC_CONTRACT_ADDRESS
    contract_abi = USDC_REAL_ABI
    print(f"✅ Using REAL USDC at: {contract_address}")

usdc_contract = w3.eth.contract(address=contract_address, abi=contract_abi)
db_pool = None

class FaucetRequest(BaseModel):
    wallet_address: str
    current_age: int
    retirement_age: int
    desired_monthly_payment: float
    monthly_deposit: float
    initial_amount: float

class FaucetResponse(BaseModel):
    success: bool
    transaction_hash: str | None
    message: str
    amount_sent: str | None
    contract_type: str | None

@app.on_event("startup")
async def startup():
    global db_pool
    
    print(f"🚀 Starting USDC Faucet Service...")
    print(f"📡 RPC URL: {RPC_URL}")
    print(f"💰 Contract: {contract_address}")
    print(f"🔧 Contract Type: {'MOCK USDC' if USE_MOCK_USDC else 'REAL USDC'}")
    print(f"🌐 CORS Origins: {CORS_ORIGINS}")

    try:
        block_number = w3.eth.block_number
        print(f"✅ Connected to blockchain. Block: {block_number}")
        symbol = usdc_contract.functions.symbol().call()
        decimals = usdc_contract.functions.decimals().call()
        print(f"✅ Contract verified. Symbol: {symbol}, Decimals: {decimals}")
    except Exception as e:
        print(f"❌ Blockchain/Contract error: {e}")
        if "MOCK" in str(e) or "symbol" in str(e):
            print("⚠️  Mock contract may not have symbol() function, continuing anyway...")
        else:
            raise

    db_pool = AsyncConnectionPool(
        conninfo=DATABASE_URL,
        min_size=1,
        max_size=10,
        open=False
    )
    await db_pool.open()
    print("✅ Database pool opened")

    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS faucet_requests (
                    id SERIAL PRIMARY KEY,
                    wallet_address VARCHAR(42) NOT NULL,
                    transaction_hash VARCHAR(66),
                    amount_sent DECIMAL(20, 6),
                    current_age INT NOT NULL,
                    retirement_age INT NOT NULL,
                    desired_monthly_payment DECIMAL(20, 2) NOT NULL,
                    monthly_deposit DECIMAL(20, 2) NOT NULL,
                    initial_amount DECIMAL(20, 2) NOT NULL,
                    status VARCHAR(20) NOT NULL,
                    error_message TEXT,
                    contract_type VARCHAR(20) DEFAULT 'real',
                    created_at TIMESTAMP DEFAULT NOW(),
                    ip_address VARCHAR(45)
                );
                
                CREATE INDEX IF NOT EXISTS idx_wallet_address ON faucet_requests(wallet_address);
                CREATE INDEX IF NOT EXISTS idx_created_at ON faucet_requests(created_at);
                CREATE INDEX IF NOT EXISTS idx_contract_type ON faucet_requests(contract_type);
            """)
            await conn.commit()
    
    print("✅ Database tables ready")
    print("🎉 Service started successfully!")

@app.on_event("shutdown")
async def shutdown():
    if db_pool:
        await db_pool.close()
        print("👋 Database pool closed")

@app.get("/")
def read_root():
    return {
        "service": "USDC Faucet",
        "status": "running",
        "version": "1.0.0",
        "contract_type": "MOCK" if USE_MOCK_USDC else "REAL",
        "contract_address": contract_address,
        "endpoints": {
            "health": "/health",
            "request_tokens": "/api/request-tokens",
            "stats": "/api/stats",
            "history": "/api/history/{wallet_address}"
        }
    }

@app.get("/health")
async def health_check():
    try:
        async with db_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1")

        block_number = w3.eth.block_number
        faucet_address = w3.eth.account.from_key(PRIVATE_KEY).address

        try:
            balance = usdc_contract.functions.balanceOf(faucet_address).call()
            decimals = usdc_contract.functions.decimals().call()
            symbol = usdc_contract.functions.symbol().call()
        except Exception as e:
            print(f"⚠️  Could not get contract details: {e}")
            decimals = 6
            symbol = "TOKEN"
            try:
                balance = usdc_contract.functions.balanceOf(faucet_address).call()
            except:
                balance = 0
        
        balance_tokens = balance / 10**decimals
        async with db_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT COUNT(*) FROM faucet_requests WHERE contract_type = %s", 
                                ('mock' if USE_MOCK_USDC else 'real',))
                total_requests = (await cur.fetchone())[0]
                
        return {
            "status": "healthy",
            "database": "connected",
            "blockchain": "connected",
            "block_number": block_number,
            "faucet_address": faucet_address,
            "contract_address": contract_address,
            "contract_type": "MOCK USDC" if USE_MOCK_USDC else "REAL USDC",
            "contract_symbol": symbol,
            "contract_decimals": decimals,
            "faucet_balance": f"{balance_tokens:.2f} {symbol}",
            "faucet_amount": f"{FAUCET_AMOUNT} {symbol}",
            "rate_limit": f"{RATE_LIMIT_HOURS} hours",
            "total_requests": total_requests,
            "cors_origins": CORS_ORIGINS
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Service unhealthy: {str(e)}")

@app.post("/api/request-tokens", response_model=FaucetResponse)
async def request_tokens(request: FaucetRequest, client_request: Request = None):
    if not w3.is_address(request.wallet_address):
        raise HTTPException(status_code=400, detail="Invalid wallet address")
    wallet_address = w3.to_checksum_address(request.wallet_address)

    if request.current_age >= request.retirement_age:
        raise HTTPException(
            status_code=400, 
            detail="Current age must be less than retirement age"
        )
    
    if request.current_age < 18 or request.retirement_age > 100:
        raise HTTPException(
            status_code=400, 
            detail="Age must be between 18 and 100"
        )
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                SELECT created_at FROM faucet_requests 
                WHERE wallet_address = %s 
                AND status = 'success'
                AND contract_type = %s
                ORDER BY created_at DESC LIMIT 1
            """, (wallet_address, 'mock' if USE_MOCK_USDC else 'real'))
            result = await cur.fetchone()
            
            if result:
                last_request = result[0]
                time_diff = datetime.utcnow() - last_request
                hours_passed = time_diff.total_seconds() / 3600
                
                if hours_passed < RATE_LIMIT_HOURS:
                    hours_left = RATE_LIMIT_HOURS - hours_passed
                    raise HTTPException(
                        status_code=429, 
                        detail=f"Please wait {hours_left:.1f} hours before requesting again"
                    )

    try:
        faucet_account = w3.eth.account.from_key(PRIVATE_KEY)

        try:
            decimals = usdc_contract.functions.decimals().call()
        except:
            decimals = 6 
        
        amount_wei = int(float(FAUCET_AMOUNT) * 10**decimals)

        faucet_balance = usdc_contract.functions.balanceOf(faucet_account.address).call()
        if faucet_balance < amount_wei:
            raise HTTPException(
                status_code=503, 
                detail=f"Faucet is empty. Balance: {faucet_balance / 10**decimals:.2f}, Required: {FAUCET_AMOUNT}"
            )

        nonce = w3.eth.get_transaction_count(faucet_account.address)
        
        if USE_MOCK_USDC:
            try:
                transaction = usdc_contract.functions.faucet(
                    wallet_address,
                    amount_wei
                ).build_transaction({
                    'from': faucet_account.address,
                    'nonce': nonce,
                    'gas': 150000,
                    'gasPrice': w3.eth.gas_price
                })
                print(f"📤 Using faucet() function for mock USDC")
            except:
                transaction = usdc_contract.functions.transfer(
                    wallet_address,
                    amount_wei
                ).build_transaction({
                    'from': faucet_account.address,
                    'nonce': nonce,
                    'gas': 100000,
                    'gasPrice': w3.eth.gas_price
                })
                print(f"📤 Using transfer() function for mock USDC")
        else:
            transaction = usdc_contract.functions.transfer(
                wallet_address,
                amount_wei
            ).build_transaction({
                'from': faucet_account.address,
                'nonce': nonce,
                'gas': 100000,
                'gasPrice': w3.eth.gas_price
            })

        signed_txn = w3.eth.account.sign_transaction(transaction, PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed_txn.raw_transaction)
        tx_hash_hex = tx_hash.hex()

        try:
            symbol = usdc_contract.functions.symbol().call()
        except:
            symbol = "USDC" if not USE_MOCK_USDC else "MOCK"

        async with db_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    INSERT INTO faucet_requests 
                    (wallet_address, transaction_hash, amount_sent, current_age, 
                     retirement_age, desired_monthly_payment, monthly_deposit, 
                     initial_amount, status, contract_type)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    wallet_address,
                    tx_hash_hex,
                    float(FAUCET_AMOUNT),
                    request.current_age,
                    request.retirement_age,
                    float(request.desired_monthly_payment),
                    float(request.monthly_deposit),
                    float(request.initial_amount),
                    'success',
                    'mock' if USE_MOCK_USDC else 'real'
                ))
                await conn.commit()
        
        print(f"✅ Sent {FAUCET_AMOUNT} {symbol} to {wallet_address}. TX: {tx_hash_hex}")
        
        return FaucetResponse(
            success=True,
            transaction_hash=tx_hash_hex,
            message=f"Successfully sent {FAUCET_AMOUNT} {symbol} to {wallet_address}",
            amount_sent=FAUCET_AMOUNT,
            contract_type="MOCK" if USE_MOCK_USDC else "REAL"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Transaction failed: {str(e)}")

        async with db_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    INSERT INTO faucet_requests 
                    (wallet_address, current_age, retirement_age, 
                     desired_monthly_payment, monthly_deposit, initial_amount, 
                     status, error_message, contract_type)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    wallet_address,
                    request.current_age,
                    request.retirement_age,
                    float(request.desired_monthly_payment),
                    float(request.monthly_deposit),
                    float(request.initial_amount),
                    'failed',
                    str(e),
                    'mock' if USE_MOCK_USDC else 'real'
                ))
                await conn.commit()
        
        raise HTTPException(status_code=500, detail=f"Transaction failed: {str(e)}")

@app.get("/api/stats")
async def get_stats():
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                SELECT 
                    COUNT(*) as total_requests,
                    COUNT(CASE WHEN status = 'success' THEN 1 END) as successful,
                    COUNT(CASE WHEN status = 'failed' THEN 1 END) as failed,
                    COALESCE(SUM(CASE WHEN status = 'success' THEN amount_sent ELSE 0 END), 0) as total_distributed,
                    COUNT(DISTINCT wallet_address) as unique_wallets,
                    COUNT(CASE WHEN contract_type = 'mock' THEN 1 END) as mock_requests,
                    COUNT(CASE WHEN contract_type = 'real' THEN 1 END) as real_requests
                FROM faucet_requests
            """)
            
            stats = await cur.fetchone()
            columns = [desc[0] for desc in cur.description]
            stats_dict = dict(zip(columns, stats))

            await cur.execute("""
                SELECT COUNT(*) 
                FROM faucet_requests 
                WHERE created_at > NOW() - INTERVAL '24 hours'
                AND status = 'success'
            """)
            last_24h = (await cur.fetchone())[0]

            await cur.execute("""
                SELECT wallet_address, COUNT(*) as request_count
                FROM faucet_requests 
                WHERE status = 'success'
                GROUP BY wallet_address 
                ORDER BY request_count DESC 
                LIMIT 5
            """)
            top_wallets = await cur.fetchall()
            
            return {
                **stats_dict,
                "requests_last_24h": last_24h,
                "top_wallets": [
                    {"address": row[0], "requests": row[1]} 
                    for row in top_wallets
                ],
                "current_contract_type": "MOCK" if USE_MOCK_USDC else "REAL",
                "current_contract_address": contract_address
            }

@app.get("/api/history/{wallet_address}")
async def get_wallet_history(wallet_address: str):
    if not w3.is_address(wallet_address):
        raise HTTPException(status_code=400, detail="Invalid wallet address")
    wallet_address = w3.to_checksum_address(wallet_address)
    
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                SELECT 
                    id, 
                    transaction_hash, 
                    amount_sent, 
                    status, 
                    created_at, 
                    current_age, 
                    retirement_age,
                    desired_monthly_payment, 
                    monthly_deposit, 
                    initial_amount,
                    error_message,
                    contract_type
                FROM faucet_requests 
                WHERE wallet_address = %s 
                ORDER BY created_at DESC 
                LIMIT 20
            """, (wallet_address,))
            
            rows = await cur.fetchall()
            columns = [desc[0] for desc in cur.description]
            
            return [dict(zip(columns, row)) for row in rows]

@app.get("/api/config")
async def get_config():
    faucet_account = w3.eth.account.from_key(PRIVATE_KEY)
    try:
        symbol = usdc_contract.functions.symbol().call()
    except:
        symbol = "UNKNOWN"
    
    try:
        decimals = usdc_contract.functions.decimals().call()
    except:
        decimals = 6
    
    return {
        "network": {
            "rpc_url": RPC_URL,
            "chain_id": w3.eth.chain_id,
            "block_number": w3.eth.block_number
        },
        "contract": {
            "address": contract_address,
            "type": "MOCK" if USE_MOCK_USDC else "REAL",
            "symbol": symbol,
            "decimals": decimals
        },
        "faucet": {
            "address": faucet_account.address,
            "amount_per_request": FAUCET_AMOUNT,
            "rate_limit_hours": RATE_LIMIT_HOURS
        },
        "service": {
            "cors_origins": CORS_ORIGINS,
            "version": "1.0.0"
        }
    }