import json
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from web3 import Web3

sys.path.append(str(Path(__file__).parent.parent))

load_dotenv()

def deploy_mock_usdc():
    RPC_URL = os.getenv("RPC_URL", "https://sepolia-rollup.arbitrum.io/rpc")
    PRIVATE_KEY = os.getenv("DEPLOYER_PRIVATE_KEY")
    CHAIN_ID = 421614 
    
    if not PRIVATE_KEY:
        raise ValueError("DEPLOYER_PRIVATE_KEY no configurada en .env")
    
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    account = w3.eth.account.from_key(PRIVATE_KEY)
    
    print(f"🚀 Desplegando Mock USDC desde: {account.address}")
    print(f"📡 RPC: {RPC_URL}")
    print(f"⛓️  Chain ID: {CHAIN_ID}")

    contract_path = Path(__file__).parent.parent / "contracts" / "MockUSDC.vy"
    with open(contract_path, 'r') as f:
        contract_source = f.read()
    
    print("🔨 Compilando contrato Vyper...")
    
    try:
        import vyper

        compiled = vyper.compile_code(
            contract_source,
            output_formats=["abi", "bytecode", "layout"]
        )
        
        abi = compiled["abi"]
        bytecode = compiled["bytecode"]
        print("✅ Contrato compilado exitosamente")
        
    except ImportError:
        print("⚠️  Vyper no instalado, intentando usar bytecode pre-compilado...")
        abi_path = Path(__file__).parent / "MockUSDC_ABI.json"
        if abi_path.exists():
            with open(abi_path, "r") as f:
                abi = json.load(f)
            bytecode = "0x"  
            
            print("⚠️  Usando ABI pre-existente. Bytecode necesario.")
            print("💡 Compila en https://vyper.online/ y pega el bytecode en el script")
            return
        
        else:
            raise Exception("Vyper no instalado y no hay ABI pre-existente")
    
    except Exception as e:
        print(f"❌ Error compilando contrato: {e}")
        print("💡 Instala vyper: pip install vyper==0.4.3")
        raise

    contract = w3.eth.contract(abi=abi, bytecode=bytecode)
    constructor_args = {
        "_name": "USD Coin",
        "_symbol": "USDC", 
        "_decimals": 6
    }

    try:
        gas_estimate = contract.constructor(
            constructor_args["_name"],
            constructor_args["_symbol"],
            constructor_args["_decimals"]
        ).estimate_gas({
            'from': account.address,
            'nonce': w3.eth.get_transaction_count(account.address)
        })
        print(f"⛽ Gas estimado: {gas_estimate}")
    except Exception as e:
        print(f"⚠️  No se pudo estimar gas: {e}")
        gas_estimate = 2000000

    latest_block = w3.eth.get_block('latest')
    base_fee = latest_block['baseFeePerGas']
    max_priority_fee = w3.to_wei(0.1, 'gwei')
    max_fee_per_gas = base_fee * 2 + max_priority_fee
    
    print(f"💰 Base Fee: {w3.from_wei(base_fee, 'gwei'):.2f} Gwei")
    print(f"💰 Max Fee Per Gas: {w3.from_wei(max_fee_per_gas, 'gwei'):.2f} Gwei")

    transaction = contract.constructor(
        constructor_args["_name"],
        constructor_args["_symbol"],
        constructor_args["_decimals"]
    ).build_transaction({
        'chainId': CHAIN_ID,
        'from': account.address,
        'nonce': w3.eth.get_transaction_count(account.address),
        'gas': int(gas_estimate * 1.5),
        'maxFeePerGas': max_fee_per_gas,
        'maxPriorityFeePerGas': max_priority_fee,
        'value': 0
    })

    print("📤 Enviando transacción de deploy...")
    signed_txn = w3.eth.account.sign_transaction(transaction, PRIVATE_KEY)
    tx_hash = w3.eth.send_raw_transaction(signed_txn.raw_transaction)
    
    print(f"📄 Transacción enviada: {tx_hash.hex()}")
    print("⏳ Esperando confirmación...")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    
    if receipt.status == 1:
        contract_address = receipt['contractAddress']
        print(f"✅ Contrato desplegado en: {contract_address}")
        print(f"🔗 Explorer: https://sepolia.arbiscan.io/address/{contract_address}")

        deploy_info = {
            "network": "arbitrum-sepolia",
            "chain_id": CHAIN_ID,
            "contract_address": contract_address,
            "contract_name": "MockUSDC",
            "deployer": account.address,
            "tx_hash": tx_hash.hex(),
            "constructor_args": constructor_args,
            "block_number": receipt['blockNumber'],
            "timestamp": w3.eth.get_block(receipt['blockNumber'])['timestamp']
        }
        output_dir = Path(__file__).parent
        with open(output_dir / "deployed_address.json", "w") as f:
            json.dump(deploy_info, f, indent=2)
        
        with open(output_dir / "MockUSDC_ABI.json", "w") as f:
            json.dump(abi, f, indent=2)
        
        print("📝 Detalles guardados en scripts/deployed_address.json")
        print("\n🔍 Verificando contrato...")
        contract_instance = w3.eth.contract(address=contract_address, abi=abi)
        
        name = contract_instance.functions.name().call()
        symbol = contract_instance.functions.symbol().call()
        decimals = contract_instance.functions.decimals().call()
        total_supply = contract_instance.functions.totalSupply().call()
        owner = contract_instance.functions.getOwner().call()
        
        print(f"✅ Token: {name} ({symbol})")
        print(f"✅ Decimals: {decimals}")
        print(f"✅ Total Supply: {total_supply / 10**decimals:,.2f} {symbol}")
        print(f"✅ Owner: {owner}")
        print(f"✅ Faucet Max: {contract_instance.functions.getFaucetMax().call() / 10**decimals} {symbol}")
        
        return contract_address, abi
        
    else:
        print("❌ Transacción falló")
        raise Exception("Deploy transaction failed")

if __name__ == "__main__":
    try:
        contract_address, abi = deploy_mock_usdc()
    except Exception as e:
        print(f"❌ Error durante deploy: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)