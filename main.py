from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from datetime import datetime
import os
from dotenv import load_dotenv
from psycopg_pool import AsyncConnectionPool
from web3 import Web3
import asyncio

load_dotenv()

app = FastAPI(
    title="USDC Faucet API",
    description="""
    🎁 **API para distribuir tokens USDC y ETH de prueba**
    
    Esta API permite a los usuarios solicitar tokens de prueba automáticamente.
    
    ## Características
    
    * 🪙 Distribuye USDC tokens (10,000 por request)
    * ⛽ Distribuye ETH para gas fees (0.001 ETH por request)
    * ⏱️ Rate limiting: 1 request cada 24 horas por wallet
    * 📊 Tracking completo de transacciones
    * 🔍 Historial por wallet
    
    ## Flujo de uso
    
    1. Usuario conecta su wallet en el frontend
    2. Frontend hace POST a `/api/request-tokens`
    3. Backend envía ETH + USDC automáticamente
    4. Frontend recibe los transaction hashes
    5. Usuario puede ver su historial en `/api/history/{address}`
    """,
    version="2.0.0",
)

CORS_ORIGINS = os.getenv("BACKEND_CORS_ORIGINS", "*").split(",")
CORS_ORIGINS = [origin.strip() for origin in CORS_ORIGINS if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS if CORS_ORIGINS != ["*"] else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
DATABASE_URL = os.getenv("DATABASE_URL")
PRIVATE_KEY = os.getenv("FAUCET_PRIVATE_KEY") or os.getenv("DEPLOYER_PRIVATE_KEY")
RPC_URL = os.getenv("RPC_URL", "https://sepolia-rollup.arbitrum.io/rpc")
MOCK_USDC_ADDRESS = os.getenv("MOCK_USDC_ADDRESS", "0x53E691B568B87f0124bb3A88C8b9958bF8396E81")
FAUCET_USDC_AMOUNT = os.getenv("FAUCET_USDC_AMOUNT", "10000")
FAUCET_ETH_AMOUNT = os.getenv("FAUCET_ETH_AMOUNT", "0.001")
RATE_LIMIT_HOURS = int(os.getenv("RATE_LIMIT_HOURS", "24"))

if not PRIVATE_KEY:
    raise ValueError("FAUCET_PRIVATE_KEY or DEPLOYER_PRIVATE_KEY required")
w3 = Web3(Web3.HTTPProvider(RPC_URL))

MOCK_USDC_ABI = [
    {
        "stateMutability": "nonpayable",
        "type": "function",
        "name": "transfer",
        "inputs": [{"name": "_to", "type": "address"}, {"name": "_value", "type": "uint256"}],
        "outputs": [{"name": "", "type": "bool"}]
    },
    {
        "stateMutability": "view",
        "type": "function",
        "name": "balanceOf",
        "inputs": [{"name": "_owner", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}]
    },
    {
        "stateMutability": "view",
        "type": "function",
        "name": "decimals",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint8"}]
    },
    {
        "stateMutability": "view",
        "type": "function",
        "name": "symbol",
        "inputs": [],
        "outputs": [{"name": "", "type": "string"}]
    }
]

usdc_contract = w3.eth.contract(address=MOCK_USDC_ADDRESS, abi=MOCK_USDC_ABI)
db_pool = None

class FaucetRequest(BaseModel):
    wallet_address: str = Field(..., description="Dirección de wallet Ethereum")
    current_age: int = Field(30, ge=18, le=100)
    retirement_age: int = Field(65, ge=18, le=100)
    desired_monthly_payment: float = Field(3000.0, gt=0)
    monthly_deposit: float = Field(500.0, ge=0)
    initial_amount: float = Field(10000.0, ge=0)

class FaucetResponse(BaseModel):
    success: bool
    usdc_transaction_hash: str | None = None
    eth_transaction_hash: str | None = None
    message: str
    usdc_amount_sent: str | None = None
    eth_amount_sent: str | None = None
    contract_type: str | None = None
    explorer_usdc_url: str | None = None
    explorer_eth_url: str | None = None

class HealthResponse(BaseModel):
    status: str
    database: str
    blockchain: str
    block_number: int
    faucet_address: str
    contract_address: str
    faucet_usdc_balance: str
    faucet_eth_balance: str
    usdc_per_request: str
    eth_per_request: str
    total_requests: int

@app.on_event("startup")
async def startup():
    global db_pool
    
    print(f"🚀 Starting USDC Faucet API v2.0.0")
    print(f"📡 RPC: {RPC_URL}")
    print(f"💰 Contract: {MOCK_USDC_ADDRESS}")
    print(f"⛽ ETH per request: {FAUCET_ETH_AMOUNT} ETH")
    print(f"🪙 USDC per request: {FAUCET_USDC_AMOUNT}")
    
    if DATABASE_URL:
        db_pool = AsyncConnectionPool(conninfo=DATABASE_URL, min_size=1, max_size=10, open=False)
        await db_pool.open()
        
        async with db_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    CREATE TABLE IF NOT EXISTS faucet_requests (
                        id SERIAL PRIMARY KEY,
                        wallet_address VARCHAR(42) NOT NULL,
                        usdc_transaction_hash VARCHAR(66),
                        eth_transaction_hash VARCHAR(66),
                        usdc_amount_sent DECIMAL(20, 6),
                        eth_amount_sent DECIMAL(20, 10),
                        current_age INT NOT NULL,
                        retirement_age INT NOT NULL,
                        desired_monthly_payment DECIMAL(20, 2) NOT NULL,
                        monthly_deposit DECIMAL(20, 2) NOT NULL,
                        initial_amount DECIMAL(20, 2) NOT NULL,
                        status VARCHAR(20) NOT NULL,
                        error_message TEXT,
                        contract_type VARCHAR(20) DEFAULT 'mock',
                        created_at TIMESTAMP DEFAULT NOW(),
                        ip_address VARCHAR(45)
                    );
                    CREATE INDEX IF NOT EXISTS idx_wallet_address ON faucet_requests(wallet_address);
                    CREATE INDEX IF NOT EXISTS idx_wallet_status ON faucet_requests(status);
                    CREATE INDEX IF NOT EXISTS idx_created_at ON faucet_requests(created_at);
                """)
                await conn.commit()
    else:
        print("⚠️ No DATABASE_URL - running without database")
    
    print("✅ Service ready!")

@app.on_event("shutdown")
async def shutdown():
    if db_pool:
        await db_pool.close()

@app.get("/")
def read_root():
    return {
        "service": "USDC Faucet API",
        "status": "running",
        "version": "2.0.0",
        "docs": "/docs",
        "endpoints": {
            "health": "/health",
            "request_tokens": "POST /api/request-tokens",
            "stats": "/api/stats",
            "history": "/api/history/{wallet_address}",
            "config": "/api/config"
        }
    }

@app.get("/health", response_model=HealthResponse)
async def health_check():
    try:
        faucet_address = w3.eth.account.from_key(PRIVATE_KEY).address
        block_number = w3.eth.block_number
        usdc_balance = usdc_contract.functions.balanceOf(faucet_address).call()
        decimals = usdc_contract.functions.decimals().call()
        symbol = usdc_contract.functions.symbol().call()
        eth_balance = w3.eth.get_balance(faucet_address)
        
        total_requests = 0
        db_status = "not configured"
        
        if db_pool:
            async with db_pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT COUNT(*) FROM faucet_requests")
                    total_requests = (await cur.fetchone())[0]
            db_status = "connected"
        
        return {
            "status": "healthy",
            "database": db_status,
            "blockchain": "connected",
            "block_number": block_number,
            "faucet_address": faucet_address,
            "contract_address": MOCK_USDC_ADDRESS,
            "faucet_usdc_balance": f"{usdc_balance / 10**decimals:,.2f} {symbol}",
            "faucet_eth_balance": f"{w3.from_wei(eth_balance, 'ether'):.4f} ETH",
            "usdc_per_request": f"{FAUCET_USDC_AMOUNT} {symbol}",
            "eth_per_request": f"{FAUCET_ETH_AMOUNT} ETH",
            "total_requests": total_requests
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Service unhealthy: {str(e)}")

@app.post("/api/request-tokens", response_model=FaucetResponse)
async def request_tokens(request: FaucetRequest):
    if not w3.is_address(request.wallet_address):
        raise HTTPException(status_code=400, detail="Invalid wallet address format")
    wallet_address = w3.to_checksum_address(request.wallet_address)

    if request.current_age >= request.retirement_age:
        raise HTTPException(status_code=400, detail="Current age must be less than retirement age")

    if db_pool:
        async with db_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    SELECT created_at FROM faucet_requests 
                    WHERE wallet_address = %s AND status = 'success'
                    ORDER BY created_at DESC LIMIT 1
                """, (wallet_address,))
                result = await cur.fetchone()
                
                if result:
                    last_request = result[0]
                    time_diff = datetime.utcnow() - last_request
                    hours_passed = time_diff.total_seconds() / 3600
                    
                    if hours_passed < RATE_LIMIT_HOURS:
                        hours_left = RATE_LIMIT_HOURS - hours_passed
                        raise HTTPException(
                            status_code=429, 
                            detail=f"Rate limit exceeded. Please wait {hours_left:.1f} hours"
                        )

    usdc_tx_hash = None
    eth_tx_hash = None
    error_messages = []

    try:
        faucet_account = w3.eth.account.from_key(PRIVATE_KEY)
        decimals = usdc_contract.functions.decimals().call()
        latest_block = w3.eth.get_block('latest')
        base_fee = latest_block['baseFeePerGas']
        max_priority_fee = w3.to_wei(0.1, 'gwei')
        max_fee_per_gas = base_fee * 2 + max_priority_fee

        try:
            eth_amount_wei = w3.to_wei(float(FAUCET_ETH_AMOUNT), 'ether')
            faucet_eth_balance = w3.eth.get_balance(faucet_account.address)
            
            if faucet_eth_balance >= eth_amount_wei + w3.to_wei(0.0001, 'ether'):  # +gas
                nonce = w3.eth.get_transaction_count(faucet_account.address)
                eth_transaction = {
                    'nonce': nonce,
                    'to': wallet_address,
                    'value': eth_amount_wei,
                    'gas': 21000,
                    'maxFeePerGas': max_fee_per_gas,
                    'maxPriorityFeePerGas': max_priority_fee,
                    'chainId': w3.eth.chain_id
                }
                signed_eth_txn = w3.eth.account.sign_transaction(eth_transaction, PRIVATE_KEY)
                eth_tx_hash_bytes = w3.eth.send_raw_transaction(signed_eth_txn.raw_transaction)
                eth_tx_hash = eth_tx_hash_bytes.hex()
                print(f"✅ ETH sent: {eth_tx_hash}")
                await asyncio.sleep(2)
            else:
                error_messages.append("Insufficient ETH balance in faucet")
        except Exception as e:
            error_messages.append(f"ETH transfer failed: {str(e)}")
            print(f"❌ ETH error: {e}")

        try:
            amount_wei = int(float(FAUCET_USDC_AMOUNT) * 10**decimals)
            nonce = w3.eth.get_transaction_count(faucet_account.address)
            
            usdc_transaction = usdc_contract.functions.transfer(
                wallet_address, amount_wei
            ).build_transaction({
                'from': faucet_account.address,
                'nonce': nonce,
                'gas': 100000,
                'maxFeePerGas': max_fee_per_gas,
                'maxPriorityFeePerGas': max_priority_fee,
                'chainId': w3.eth.chain_id
            })
            
            signed_usdc_txn = w3.eth.account.sign_transaction(usdc_transaction, PRIVATE_KEY)
            usdc_tx_hash_bytes = w3.eth.send_raw_transaction(signed_usdc_txn.raw_transaction)
            usdc_tx_hash = usdc_tx_hash_bytes.hex()
            print(f"✅ USDC sent: {usdc_tx_hash}")
        except Exception as e:
            error_messages.append(f"USDC transfer failed: {str(e)}")
            print(f"❌ USDC error: {e}")

        if not usdc_tx_hash and not eth_tx_hash:
            raise HTTPException(status_code=500, detail="; ".join(error_messages))

        status = 'success' if (usdc_tx_hash or eth_tx_hash) else 'failed'
        if db_pool:
            async with db_pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("""
                        INSERT INTO faucet_requests 
                        (wallet_address, usdc_transaction_hash, eth_transaction_hash,
                         usdc_amount_sent, eth_amount_sent, current_age, retirement_age,
                         desired_monthly_payment, monthly_deposit, initial_amount, status, 
                         error_message, contract_type)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        wallet_address, usdc_tx_hash, eth_tx_hash,
                        float(FAUCET_USDC_AMOUNT) if usdc_tx_hash else None,
                        float(FAUCET_ETH_AMOUNT) if eth_tx_hash else None,
                        request.current_age, request.retirement_age,
                        float(request.desired_monthly_payment), float(request.monthly_deposit),
                        float(request.initial_amount), status,
                        '; '.join(error_messages) if error_messages else None, 'mock'
                    ))
                    await conn.commit()
        
        message_parts = []
        if usdc_tx_hash:
            message_parts.append(f"{FAUCET_USDC_AMOUNT} USDC")
        if eth_tx_hash:
            message_parts.append(f"{FAUCET_ETH_AMOUNT} ETH")
        
        return FaucetResponse(
            success=True,
            usdc_transaction_hash=usdc_tx_hash,
            eth_transaction_hash=eth_tx_hash,
            message=f"Successfully sent {' + '.join(message_parts)} to {wallet_address}",
            usdc_amount_sent=FAUCET_USDC_AMOUNT if usdc_tx_hash else None,
            eth_amount_sent=FAUCET_ETH_AMOUNT if eth_tx_hash else None,
            contract_type="MOCK",
            explorer_usdc_url=f"https://sepolia.arbiscan.io/tx/{usdc_tx_hash}" if usdc_tx_hash else None,
            explorer_eth_url=f"https://sepolia.arbiscan.io/tx/{eth_tx_hash}" if eth_tx_hash else None
        )
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error: {e}")
        if db_pool:
            async with db_pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("""
                        INSERT INTO faucet_requests 
                        (wallet_address, current_age, retirement_age, desired_monthly_payment,
                         monthly_deposit, initial_amount, status, error_message, contract_type)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        wallet_address, request.current_age, request.retirement_age,
                        float(request.desired_monthly_payment), float(request.monthly_deposit),
                        float(request.initial_amount), 'failed', str(e), 'mock'
                    ))
                    await conn.commit()
        raise HTTPException(status_code=500, detail=f"Transaction failed: {str(e)}")

@app.get("/api/history/{wallet_address}")
async def get_wallet_history(wallet_address: str):
    if not db_pool:
        return []
    
    if not w3.is_address(wallet_address):
        raise HTTPException(status_code=400, detail="Invalid wallet address")
    wallet_address = w3.to_checksum_address(wallet_address)
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                SELECT id, usdc_transaction_hash, eth_transaction_hash,
                       usdc_amount_sent, eth_amount_sent, status, created_at, contract_type
                FROM faucet_requests 
                WHERE wallet_address = %s 
                ORDER BY created_at DESC 
                LIMIT 20
            """, (wallet_address,))
            
            rows = await cur.fetchall()
            columns = [desc[0] for desc in cur.description]
            return [dict(zip(columns, row)) for row in rows]

@app.get("/api/stats")
async def get_stats():
    if not db_pool:
        return {
            "total_requests": 0,
            "successful": 0,
            "failed": 0,
            "total_usdc": 0,
            "total_eth": 0,
            "unique_wallets": 0
        }
    
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                SELECT 
                    COUNT(*) as total_requests,
                    COUNT(CASE WHEN status = 'success' THEN 1 END) as successful,
                    COUNT(CASE WHEN status = 'failed' THEN 1 END) as failed,
                    COALESCE(SUM(CASE WHEN status = 'success' THEN usdc_amount_sent ELSE 0 END), 0) as total_usdc,
                    COALESCE(SUM(CASE WHEN status = 'success' THEN eth_amount_sent ELSE 0 END), 0) as total_eth,
                    COUNT(DISTINCT wallet_address) as unique_wallets
                FROM faucet_requests
            """)
            stats = await cur.fetchone()
            columns = [desc[0] for desc in cur.description]
            return dict(zip(columns, stats))

@app.get("/api/config")
async def get_config():
    faucet_account = w3.eth.account.from_key(PRIVATE_KEY)
    return {
        "network": {
            "name": "Arbitrum Sepolia",
            "rpc_url": RPC_URL,
            "chain_id": w3.eth.chain_id,
            "explorer": "https://sepolia.arbiscan.io"
        },
        "contract": {
            "address": MOCK_USDC_ADDRESS,
            "type": "MOCK USDC"
        },
        "faucet": {
            "address": faucet_account.address,
            "usdc_per_request": FAUCET_USDC_AMOUNT,
            "eth_per_request": FAUCET_ETH_AMOUNT,
            "rate_limit_hours": RATE_LIMIT_HOURS
        }
    }