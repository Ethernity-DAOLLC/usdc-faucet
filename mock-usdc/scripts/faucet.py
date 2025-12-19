import json
import os
from web3 import Web3
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

class MockUSDCFaucet:
    def __init__(self, contract_address=None):
        self.w3 = Web3(Web3.HTTPProvider(
            "https://sepolia-rollup.arbitrum.io/rpc"
        ))
        self.private_key = os.getenv("FAUCET_PRIVATE_KEY")
        self.account = self.w3.eth.account.from_key(self.private_key)

        with open(Path(__file__).parent / "MockUSDC_ABI.json", "r") as f:
            self.abi = json.load(f)
        
        self.contract_address = contract_address or self.load_deployed_address()
        self.contract = self.w3.eth.contract(
            address=self.contract_address,
            abi=self.abi
        )
    
    def load_deployed_address(self):
        with open(Path(__file__).parent / "deployed_address.json", "r") as f:
            data = json.load(f)
        return data["address"]
    
    def send_tokens(self, to_address, amount=100):
        decimals = self.contract.functions.decimals().call()
        amount_wei = amount * (10 ** decimals)
        
        tx = self.contract.functions.transfer(
            to_address,
            amount_wei
        ).build_transaction({
            'chainId': 421614,
            'from': self.account.address,
            'nonce': self.w3.eth.get_transaction_count(self.account.address),
            'gas': 100000,
            'gasPrice': self.w3.eth.gas_price,
        })
        
        signed = self.w3.eth.account.sign_transaction(tx, self.private_key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        
        print(f"✅ Enviados {amount} mUSDC a {to_address}")
        print(f"📄 Tx: https://sepolia.arbiscan.io/tx/{tx_hash.hex()}")
        return tx_hash.hex()
    
    def faucet_public(self, to_address, amount=10):
        decimals = self.contract.functions.decimals().call()
        amount_wei = amount * (10 ** decimals)
        
        tx = self.contract.functions.faucet(
            to_address,
            amount_wei
        ).build_transaction({
            'chainId': 421614,
            'from': to_address,
            'nonce': self.w3.eth.get_transaction_count(to_address),
            'gas': 100000,
            'gasPrice': self.w3.eth.gas_price,
        })
        return tx

if __name__ == "__main__":
    testers = [
        "0x742d35Cc6634C0532925a3b844Bc9eE6a5Ae823A",
        "0x53d284357ec70cE289D6D64134DfAc8E511c8a3D"
    ]
    
    faucet = MockUSDCFaucet()
    
    for tester in testers:
        try:
            faucet.send_tokens(tester, 10000)
        except Exception as e:
            print(f"❌ Error con {tester}: {e}")