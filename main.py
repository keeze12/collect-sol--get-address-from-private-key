import time
import os
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solana.rpc.async_api import AsyncClient
from solders.system_program import TransferParams, transfer
from solana.transaction import Transaction
from spl.token.instructions import transfer_checked, get_associated_token_address, TransferCheckedParams
from spl.token.constants import TOKEN_PROGRAM_ID
import asyncio
import random
import base58
from data.config import RPC_URLS
from utils import logger

# Создание директорий для логов и данных, если они не существуют
os.makedirs('logs', exist_ok=True)
os.makedirs('data', exist_ok=True)


async def send_sol(client, sender, receiver, amount):
    transaction = Transaction().add(transfer(
        TransferParams(
            from_pubkey=sender.pubkey(),
            to_pubkey=Pubkey.from_string(receiver),
            lamports=int(amount * 1e9)
        )
    ))
    return await client.send_transaction(transaction, sender)


async def send_sol_to_addresses(params):
    network_url = params['network_url']
    addresses = params['addresses']
    min_amount = params['min_amount']
    max_amount = params['max_amount']
    private_key = params['private_key']

    logger.info(f"Начало отправки SOL. URL сети: {network_url}")

    total_sol_sent = 0

    async with AsyncClient(network_url) as client:
        try:
            private_key_bytes = base58.b58decode(private_key)
            sender = Keypair.from_bytes(private_key_bytes)
            logger.info(f"Кошелек отправителя: {sender.pubkey()}")
        except Exception as e:
            logger.error(f"Ошибка декодирования приватного ключа: {e}")
            return {'total_attempts': 0, 'successful_sends': 0, 'total_sol_sent': 0}

        total_attempts = 0
        successful_sends = 0

        for address in addresses:
            success = False
            attempts = 0
            while not success and attempts < 3:
                attempts += 1
                total_attempts += 1
                amount = random.uniform(min_amount, max_amount)
                try:
                    logger.info(f"Попытка отправить {amount} SOL на {address}")
                    signature = await send_sol(client, sender, address, amount)
                    success = True
                    successful_sends += 1
                    total_sol_sent += amount

                    solscan_url = f"https://solscan.io/tx/{signature.value}"

                    logger.info(f"Успешно отправлено {amount} SOL на {address}. Подпись: {signature.value}. {solscan_url}")
                except Exception as e:
                    logger.error(f"Ошибка при отправке SOL на {address}: {e}")
                    await asyncio.sleep(1)

    return {
        'total_attempts': total_attempts,
        'successful_sends': successful_sends,
        'total_sol_sent': total_sol_sent
    }


async def get_token_info(network_url, token_contract):
    async with AsyncClient(network_url) as client:
        try:
            token_pubkey = Pubkey.from_string(token_contract)
            token_info = await client.get_token_supply(token_pubkey)
            return {
                'decimals': token_info.value.decimals
            }
        except Exception as e:
            logger.error(f"Ошибка получения информации о токене: {e}")
            return None


async def collect_tokens_from_addresses(network_url, token_contract, recipient, keys):
    async with AsyncClient(network_url) as client:
        results = []
        total_tokens = 0
        token_info = await get_token_info(network_url, token_contract)
        decimals = token_info['decimals'] if token_info else 0

        for private_key in keys:
            try:
                sender = Keypair.from_bytes(base58.b58decode(private_key))
                token_pubkey = Pubkey.from_string(token_contract)
                recipient_pubkey = Pubkey.from_string(recipient)

                sender_ata = get_associated_token_address(sender.pubkey(), token_pubkey)
                recipient_ata = get_associated_token_address(recipient_pubkey, token_pubkey)

                balance = await client.get_token_account_balance(sender_ata)
                amount = int(balance.value.amount)

                if amount > 0:
                    tx = Transaction().add(
                        transfer_checked(
                            TransferCheckedParams(
                                program_id=TOKEN_PROGRAM_ID,
                                source=sender_ata,
                                mint=token_pubkey,
                                dest=recipient_ata,
                                owner=sender.pubkey(),
                                amount=amount,
                                decimals=decimals
                            )
                        )
                    )

                    signature = await client.send_transaction(tx, sender)

                    total_tokens += amount
                    human_readable_amount = amount / (10 ** decimals)

                    results.append({
                        'success': True,
                        'sender': str(sender.pubkey()),
                        'amount': human_readable_amount,
                        'signature': str(signature)
                    })
                else:
                    logger.info(f"Отправитель {sender.pubkey()} не имеет токенов")
                    results.append({
                        'success': False,
                        'sender': str(sender.pubkey()),
                        'error': 'Недостаточно токенов'
                    })
            except Exception as e:
                results.append({
                    'success': False,
                    'sender': str(sender.pubkey()) if 'sender' in locals() else 'Неизвестно',
                    'error': str(e)
                })

        return results, total_tokens, decimals


async def send_all_sol(client, sender, receiver):
    try:
        # Получаем баланс отправителя
        balance_response = await client.get_balance(sender.pubkey())
        balance = balance_response.value
        logger.info(f"Баланс отправителя {sender.pubkey()}: {balance} лампортов")

        # Проверяем, достаточно ли средств
        if balance <= 5000:  # Минимальный баланс для комиссии
            logger.info(f"Недостаточно средств на {sender.pubkey()} для отправки.")
            return None

        # Рассчитываем сумму для отправки
        lamports_to_send = balance - 5000  # Оставляем для комиссии
        logger.info(f"Сумма для отправки: {lamports_to_send} лампортов")

        # Получаем свежий блокхэш
        recent_blockhash_response = await client.get_latest_blockhash()
        recent_blockhash = recent_blockhash_response.value.blockhash
        logger.info(f"Свежий блокхэш: {recent_blockhash}")

        # Создаем транзакцию
        transaction = Transaction(recent_blockhash=recent_blockhash).add(transfer(
            TransferParams(
                from_pubkey=sender.pubkey(),
                to_pubkey=Pubkey.from_string(receiver),
                lamports=lamports_to_send
            )
        ))

        # Подписываем транзакцию
        transaction.sign(sender)
        logger.info(f"Создана транзакция для отправки: {transaction}")

        # Отправляем сериализованную транзакцию
        signature = await client.send_raw_transaction(transaction.serialize())
        logger.info(f"Транзакция отправлена с подписью: {signature}")

        return signature

    except Exception as e:
        logger.error(f"Ошибка при отправке SOL с {sender.pubkey()} на {receiver}: {str(e)}")
        return None


async def send_all_sol_from_keys_to_addresses(params):
    network_url = params['network_url']
    addresses = params['addresses']
    keys = params['keys']

    logger.info(f"Начало отправки всех SOL. URL сети: {network_url}")

    total_attempts = 0
    successful_sends = 0

    async with AsyncClient(network_url) as client:
        for key in keys:
            try:
                private_key_bytes = base58.b58decode(key)
                sender = Keypair.from_bytes(private_key_bytes)
                logger.info(f"Кошелек отправителя: {sender.pubkey()}")
            except Exception as e:
                logger.error(f"Ошибка декодирования приватного ключа: {e}")
                continue

            for address in addresses:
                total_attempts += 1
                logger.info(f"Попытка отправить все SOL с {sender.pubkey()} на {address}")
                signature = await send_all_sol(client, sender, address)

                if signature:
                    successful_sends += 1
                    solscan_url = f"https://solscan.io/tx/{signature.value}"
                    logger.info(f"Успешно отправлено. Подпись транзакции: {signature.value}. {solscan_url}")
                else:
                    logger.error(f"Не удалось отправить SOL с {sender.pubkey()} на {address}")

    return {
        'total_attempts': total_attempts,
        'successful_sends': successful_sends
    }


def load_from_file(filename):
    filepath = os.path.join('data', filename)
    try:
        with open(filepath, 'r') as file:
            return [line.strip() for line in file if line.strip()]
    except FileNotFoundError:
        logger.error(f"Ошибка: {filepath} не найден")
        return []


async def save_wallets_from_keys(keys):
    addresses = []
    for key in keys:
        try:
            private_key_bytes = base58.b58decode(key)
            sender = Keypair.from_bytes(private_key_bytes)
            addresses.append(str(sender.pubkey()))
        except Exception as e:
            logger.error(f"Ошибка при извлечении адреса из приватного ключа: {e}")

    # Записываем адреса в wallets.txt
    filepath = os.path.join('data', 'wallets.txt')
    with open(filepath, 'w') as file:
        for address in addresses:
            file.write(address + '\n')
    
    logger.info(f"Адреса успешно записаны в {filepath}")

async def main():
    network_url = RPC_URLS["MAINNET"]
    logger.info("Запуск Solana Transfer Tool")
    await asyncio.sleep(0.1)

    while True:
        print("\n=== Solana Transfer Menu ===")
        print("1. Отправить SOL на несколько адресов")
        print("2. Собрать токены с нескольких кошельков")
        print("3. Отправить все SOL с ключей на адреса")
        print("4. Получить адреса из приватных ключей и записать в wallets.txt")
        print("5. Выйти")
        
        choice = input("\nВведите ваш выбор (1-5): ")
        
        if choice == "1":
            addresses = load_from_file('addresses.txt')
            if not addresses:
                logger.error("addresses.txt не найден или пуст")
                continue
                
            private_key = input("Введите приватный ключ отправителя: ")
            min_amount = float(input("Введите минимальное количество SOL: "))
            max_amount = float(input("Введите максимальное количество SOL: "))
            
            params = {
                'network_url': network_url,
                'addresses': addresses,
                'min_amount': min_amount,
                'max_amount': max_amount,
                'private_key': private_key
            }
            
            result = await send_sol_to_addresses(params)
            print(f"\nПеревод завершён:")
            print(f"Успешные переводы: {result['successful_sends']}/{result['total_attempts']}")
            print(f"Всего отправлено SOL: {result['total_sol_sent']:.4f}")
            
        elif choice == "2":
            keys = load_from_file('keys.txt')
            if not keys:
                print("Пожалуйста, убедитесь, что keys.txt существует с приватными ключами")
                continue
                
            token_contract = input("Введите адрес токен-контракта: ")
            recipient = input("Введите адрес получателя: ")
            
            results, total_tokens, decimals = await collect_tokens_from_addresses(
                network_url, token_contract, recipient, keys
            )
            
            print(f"\nСбор токенов завершён:")
            print(f"Всего собранных токенов: {total_tokens / (10 ** decimals):.6f}")
            print(f"Успешные переводы: {sum(1 for r in results if r['success'])}/{len(results)}")
            
        elif choice == "3":
            addresses = load_from_file('addresses.txt')
            if not addresses:
                logger.error("addresses.txt не найден или пуст")
                continue

            keys = load_from_file('keys.txt')
            if not keys:
                logger.error("keys.txt не найден или пуст")
                continue
                
            params = {
                'network_url': network_url,
                'addresses': addresses,
                'keys': keys
            }

            result = await send_all_sol_from_keys_to_addresses(params)
            print(f"\nПереводы завершены:")
            print(f"Всего попыток: {result['total_attempts']}, Успешные переводы: {result['successful_sends']}")

        elif choice == "4":
            keys = load_from_file('keys.txt')
            if not keys:
                logger.error("keys.txt не найден или пуст")
                continue
            
            await save_wallets_from_keys(keys)
            print("Адреса успешно сохранены в wallets.txt.")

        elif choice == "5":
            print("Выход...")
            break
        else:
            print("Неверный выбор. Пожалуйста, попробуйте снова.")

if __name__ == "__main__":
    asyncio.run(main())
