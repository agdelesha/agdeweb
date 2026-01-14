#!/bin/bash
# Скрипт удаления клиента AmneziaWG
# Использование: ./awg-remove-client.sh <username> [public_key]

set -e

AWG_CONFIG="/etc/amnezia/amneziawg/awg0.conf"
CLIENT_DIR="/etc/amnezia/amneziawg/clients"

USERNAME="$1"
PUBLIC_KEY="$2"

if [ -z "$USERNAME" ]; then
    echo "Usage: $0 <username> [public_key]"
    exit 1
fi

# Если public_key не передан, пытаемся найти по имени в комментарии
if [ -z "$PUBLIC_KEY" ]; then
    # Ищем блок [Peer] с комментарием # $USERNAME
    PUBLIC_KEY=$(grep -A1 "# $USERNAME" $AWG_CONFIG | grep PublicKey | awk '{print $3}')
fi

if [ -n "$PUBLIC_KEY" ]; then
    # Создаём временный файл без этого пира
    awk -v pk="$PUBLIC_KEY" '
    BEGIN { skip=0 }
    /^\[Peer\]/ { 
        peer_block=$0; 
        skip=0; 
        next 
    }
    /^PublicKey/ { 
        if ($3 == pk) { 
            skip=1 
        } else if (peer_block != "") { 
            print peer_block 
        }
        peer_block=""
    }
    { 
        if (!skip) print 
    }
    ' $AWG_CONFIG > ${AWG_CONFIG}.tmp
    
    mv ${AWG_CONFIG}.tmp $AWG_CONFIG
fi

# Удаляем файлы клиента
rm -f "$CLIENT_DIR/$USERNAME.conf" "$CLIENT_DIR/$USERNAME.png"

# Перезапускаем AWG
systemctl restart awg-quick@awg0

echo "OK: $USERNAME removed"
