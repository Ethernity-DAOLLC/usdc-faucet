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
        self.private_key = os.getenv("FAUCET_PRIVATE_KEY") or os.getenv("DEPLOYER_PRIVATE_KEY")
        
        if not self.private_key:
            raise ValueError("Necesitas configurar FAUCET_PRIVATE_KEY o DEPLOYER_PRIVATE_KEY en .env")
        
        self.account = self.w3.eth.account.from_key(self.private_key)
        print(f"💰 Usando wallet: {self.account.address}")

        abi_path = Path(__file__).parent / "MockUSDC_ABI.json"
        with open(abi_path, "r") as f:
            self.abi = json.load(f)
        
        self.contract_address = contract_address or self.load_deployed_address()
        self.contract = self.w3.eth.contract(
            address=self.contract_address,
            abi=self.abi
        )
        print(f"📄 Contrato: {self.contract_address}")
    
    def load_deployed_address(self):
        deployed_path = Path(__file__).parent / "deployed_address.json"
        with open(deployed_path, "r") as f:
            data = json.load(f)
        return data["contract_address"]
    
    def get_balance(self, address):
        balance = self.contract.functions.balanceOf(address).call()
        decimals = self.contract.functions.decimals().call()
        return balance / (10 ** decimals)
    
    def send_tokens(self, to_address, amount=100):
        decimals = self.contract.functions.decimals().call()
        amount_wei = int(amount * (10 ** decimals))
        latest_block = self.w3.eth.get_block('latest')
        base_fee = latest_block['baseFeePerGas']
        max_priority_fee = self.w3.to_wei(0.1, 'gwei')
        max_fee_per_gas = base_fee * 2 + max_priority_fee
        
        tx = self.contract.functions.transfer(
            to_address,
            amount_wei
        ).build_transaction({
            'chainId': 421614,
            'from': self.account.address,
            'nonce': self.w3.eth.get_transaction_count(self.account.address),
            'gas': 100000,
            'maxFeePerGas': max_fee_per_gas,
            'maxPriorityFeePerGas': max_priority_fee,
        })
        
        signed = self.w3.eth.account.sign_transaction(tx, self.private_key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        
        print(f"✅ Enviados {amount} mUSDC a {to_address}")
        print(f"📄 Tx: https://sepolia.arbiscan.io/tx/{tx_hash.hex()}")
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
        if receipt.status == 1:
            new_balance = self.get_balance(to_address)
            print(f"✅ Confirmado! Nuevo balance: {new_balance} mUSDC")
        else:
            print(f"❌ Transacción falló")
        
        return tx_hash.hex()
    
    def mint_tokens(self, to_address, amount=100):
        decimals = self.contract.functions.decimals().call()
        amount_wei = int(amount * (10 ** decimals))
        latest_block = self.w3.eth.get_block('latest')
        base_fee = latest_block['baseFeePerGas']
        max_priority_fee = self.w3.to_wei(0.1, 'gwei')
        max_fee_per_gas = base_fee * 2 + max_priority_fee
        
        tx = self.contract.functions.mint(
            to_address,
            amount_wei
        ).build_transaction({
            'chainId': 421614,
            'from': self.account.address,
            'nonce': self.w3.eth.get_transaction_count(self.account.address),
            'gas': 100000,
            'maxFeePerGas': max_fee_per_gas,
            'maxPriorityFeePerGas': max_priority_fee,
        })
        
        signed = self.w3.eth.account.sign_transaction(tx, self.private_key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        print(f"✅ Minteados {amount} mUSDC a {to_address}")
        print(f"📄 Tx: https://sepolia.arbiscan.io/tx/{tx_hash.hex()}")
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
        
        if receipt.status == 1:
            new_balance = self.get_balance(to_address)
            print(f"✅ Confirmado! Nuevo balance: {new_balance} mUSDC")
        else:
            print(f"❌ Transacción falló")
        return tx_hash.hex()

if __name__ == "__main__":
    testers = [
        "0x742d35Cc6634C0532925a3b844Bc9eE6a5Ae823A",
        "0x53d284357ec70cE289D6D64134DfAc8E511c8a3D",
        "0x2c81Af5Ca0663Ef8aa73b498c0E5BeC54EB24C15"
    ]
    faucet = MockUSDCFaucet()
    testers = [Web3.to_checksum_address(addr) for addr in testers]
    faucet_balance = faucet.get_balance(faucet.account.address)
    print(f"\n💰 Balance del faucet: {faucet_balance:,.2f} mUSDC\n")

    for tester in testers:
        try:
            print(f"\n🎯 Enviando tokens a {tester}...")
            faucet.send_tokens(tester, 10000)
        except Exception as e:
            print(f"❌ Error con {tester}: {e}")

    print(f"\n💰 Balance final del faucet: {faucet.get_balance(faucet.account.address):,.2f} mUSDC")