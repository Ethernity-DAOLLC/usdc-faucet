from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime
import os
from dotenv import load_dotenv
from psycopg_pool import AsyncConnectionPool
from web3 import Web3
from decimal import Decimal
import asyncio

load_dotenv()

app = FastAPI(title="USDC Faucet Service", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://tu-frontend.vercel.app",
        "http://localhost:3000",
        "http://localhost:5173"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATABASE_URL = os.getenv("DATABASE_URL")
PRIVATE_KEY = os.getenv("FAUCET_PRIVATE_KEY")
RPC_URL = os.getenv("RPC_URL")
USDC_CONTRACT_ADDRESS = os.getenv("USDC_CONTRACT_ADDRESS")
FAUCET_AMOUNT = os.getenv("FAUCET_AMOUNT", "1000")

w3 = Web3(Web3.HTTPProvider(RPC_URL))

USDC_ABI = [
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
    }
]

usdc_contract = w3.eth.contract(address=USDC_CONTRACT_ADDRESS, abi=USDC_ABI)
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

@app.on_event("startup")
async def startup():
    global db_pool
    db_pool = AsyncConnectionPool(
        conninfo=DATABASE_URL,
        min_size=1,
        max_size=10,
        open=False
    )
    await db_pool.open()

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
                    created_at TIMESTAMP DEFAULT NOW(),
                    ip_address VARCHAR(45)
                );
                
                CREATE INDEX IF NOT EXISTS idx_wallet_address ON faucet_requests(wallet_address);
                CREATE INDEX IF NOT EXISTS idx_created_at ON faucet_requests(created_at);
            """)
            await conn.commit()

@app.on_event("shutdown")
async def shutdown():
    if db_pool:
        await db_pool.close()

@app.get("/")
def read_root():
    return {
        "service": "USDC Faucet",
        "status": "running",
        "version": "1.0.0"
    }

@app.get("/health")
async def health_check():
    try:
        async with db_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1")
        block_number = w3.eth.block_number

        faucet_address = w3.eth.account.from_key(PRIVATE_KEY).address
        balance = usdc_contract.functions.balanceOf(faucet_address).call()
        
        return {
            "status": "healthy",
            "database": "connected",
            "blockchain": "connected",
            "block_number": block_number,
            "faucet_balance": str(balance / 10**6) + " USDC"
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Service unhealthy: {str(e)}")

@app.post("/api/request-tokens", response_model=FaucetResponse)
async def request_tokens(request: FaucetRequest):
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
                WHERE wallet_address = %s AND status = 'success'
                ORDER BY created_at DESC LIMIT 1
            """, (wallet_address,))
            result = await cur.fetchone()
            
            if result:
                last_request = result[0]
                time_diff = datetime.utcnow() - last_request
                if time_diff.total_seconds() < 86400:
                    hours_left = 24 - (time_diff.total_seconds() / 3600)
                    raise HTTPException(
                        status_code=429, 
                        detail=f"Please wait {hours_left:.1f} hours before requesting again"
                    )
    
    try:
        faucet_account = w3.eth.account.from_key(PRIVATE_KEY)
        amount_wei = int(float(FAUCET_AMOUNT) * 10**6)

        faucet_balance = usdc_contract.functions.balanceOf(faucet_account.address).call()
        if faucet_balance < amount_wei:
            raise HTTPException(
                status_code=503, 
                detail="Faucet is empty. Please contact administrator."
            )

        nonce = w3.eth.get_transaction_count(faucet_account.address)
        
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

        async with db_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    INSERT INTO faucet_requests 
                    (wallet_address, transaction_hash, amount_sent, current_age, 
                     retirement_age, desired_monthly_payment, monthly_deposit, 
                     initial_amount, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    wallet_address,
                    tx_hash_hex,
                    float(FAUCET_AMOUNT),
                    request.current_age,
                    request.retirement_age,
                    float(request.desired_monthly_payment),
                    float(request.monthly_deposit),
                    float(request.initial_amount),
                    'success'
                ))
                await conn.commit()
        
        return FaucetResponse(
            success=True,
            transaction_hash=tx_hash_hex,
            message=f"Successfully sent {FAUCET_AMOUNT} USDC to {wallet_address}",
            amount_sent=FAUCET_AMOUNT
        )
        
    except Exception as e:
        async with db_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    INSERT INTO faucet_requests 
                    (wallet_address, current_age, retirement_age, 
                     desired_monthly_payment, monthly_deposit, initial_amount, 
                     status, error_message)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    wallet_address,
                    request.current_age,
                    request.retirement_age,
                    float(request.desired_monthly_payment),
                    float(request.monthly_deposit),
                    float(request.initial_amount),
                    'failed',
                    str(e)
                ))
                await conn.commit()
        
        raise HTTPException(status_code=500, detail=f"Transaction failed: {str(e)}")

@app.get("/api/stats")
async def get_stats():
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT COUNT(*) FROM faucet_requests")
            total_requests = (await cur.fetchone())[0]
            
            await cur.execute("SELECT COUNT(*) FROM faucet_requests WHERE status = 'success'")
            successful = (await cur.fetchone())[0]
            
            await cur.execute("SELECT COALESCE(SUM(amount_sent), 0) FROM faucet_requests WHERE status = 'success'")
            total_distributed = (await cur.fetchone())[0]
            
            return {
                "total_requests": total_requests,
                "successful_requests": successful,
                "failed_requests": total_requests - successful,
                "total_usdc_distributed": float(total_distributed or 0)
            }

@app.get("/api/history/{wallet_address}")
async def get_wallet_history(wallet_address: str):
    if not w3.is_address(wallet_address):
        raise HTTPException(status_code=400, detail="Invalid wallet address")
    
    wallet_address = w3.to_checksum_address(wallet_address)
    
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                SELECT id, transaction_hash, amount_sent, status, 
                       created_at, current_age, retirement_age,
                       desired_monthly_payment, monthly_deposit, initial_amount
                FROM faucet_requests 
                WHERE wallet_address = %s 
                ORDER BY created_at DESC 
                LIMIT 10
            """, (wallet_address,))
            
            rows = await cur.fetchall()
            columns = [desc[0] for desc in cur.description]
            
            return [dict(zip(columns, row)) for row in rows]