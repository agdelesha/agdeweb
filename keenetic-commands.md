# Keenetic WireGuard Configuration Commands

## Подключение к роутеру через SSH

```bash
ssh admin@ROUTER_IP_OR_DOMAIN
```

## 1. Настройка WireGuard интерфейса

После подключения к роутеру выполните следующие команды:

```bash
# Войти в режим конфигурации
configure

# Создать WireGuard интерфейс
interface Wireguard0
  description "WireGuard VPN"
  ip address YOUR_VPN_IP/32
  private-key YOUR_PRIVATE_KEY
  mtu 1420
  up
  exit

# Добавить peer (VPN сервер)
interface Wireguard0
  peer
    public-key SERVER_PUBLIC_KEY
    preshared-key YOUR_PRESHARED_KEY
    endpoint SERVER_IP:PORT
    allowed-ips 0.0.0.0/0
    persistent-keepalive 25
    exit
  exit

# Сохранить конфигурацию
system configuration save
exit
```

## 2. Проверка статуса WireGuard

```bash
show interface Wireguard0
show interface Wireguard0 peer
```

## 3. Настройка селективной маршрутизации

### Вариант A: Через NDMS CLI (если поддерживается)

```bash
configure

# Создать таблицу маршрутизации для VPN
ip route table 100 name vpn-table
ip route table 100 0.0.0.0/0 Wireguard0

# Создать IP sets для сервисов
ip set create youtube hash:net
ip set create instagram hash:net
ip set create openai hash:net

# Добавить IP-адреса YouTube
ip set add youtube 172.217.0.0/16
ip set add youtube 216.58.192.0/19
ip set add youtube 142.250.0.0/15

# Добавить IP-адреса Instagram (Meta)
ip set add instagram 31.13.24.0/21
ip set add instagram 31.13.64.0/18
ip set add instagram 157.240.0.0/16

# Добавить IP-адреса OpenAI
ip set add openai 104.18.0.0/20
ip set add openai 172.64.0.0/13

# Создать правила маршрутизации
ip rule add fwmark 100 table 100 priority 100

# Создать правила firewall для маркировки пакетов
firewall rule add chain=prerouting match-set=youtube dst action=mark mark=100
firewall rule add chain=prerouting match-set=instagram dst action=mark mark=100
firewall rule add chain=prerouting match-set=openai dst action=mark mark=100

# Сохранить конфигурацию
system configuration save
exit
```

### Вариант B: Через веб-интерфейс (если CLI не поддерживает ipset)

1. **Интернет → Другие подключения → WireGuard**
   - Настройте интерфейс с вашими данными

2. **Интернет → Маршруты**
   - Добавьте статические маршруты для каждого IP-диапазона:
   
   **YouTube:**
   - 172.217.0.0/16 → Wireguard0
   - 216.58.192.0/19 → Wireguard0
   - 142.250.0.0/15 → Wireguard0
   
   **Instagram:**
   - 31.13.24.0/21 → Wireguard0
   - 31.13.64.0/18 → Wireguard0
   - 157.240.0.0/16 → Wireguard0
   
   **OpenAI:**
   - 104.18.0.0/20 → Wireguard0
   - 172.64.0.0/13 → Wireguard0

## 4. Получение актуальных IP-адресов сервисов

Для получения актуальных IP-адресов используйте:

```bash
# YouTube
nslookup youtube.com
nslookup googlevideo.com

# Instagram
nslookup instagram.com
nslookup cdninstagram.com

# OpenAI
nslookup chat.openai.com
nslookup api.openai.com
```

## 5. Проверка маршрутизации

```bash
# Проверить маршруты
show ip route

# Проверить правила
show ip rule

# Проверить IP sets (если используются)
show ip set

# Проверить firewall правила
show firewall rule
```

## 6. Тестирование

После настройки проверьте:

```bash
# С устройства в сети роутера
curl -s https://api.ipify.org  # Должен показать ваш обычный IP
curl -s --resolve chat.openai.com:443:IP_FROM_NSLOOKUP https://chat.openai.com  # Должен работать через VPN
```

## 7. Отладка

Если что-то не работает:

```bash
# Проверить логи
show log

# Проверить статус интерфейса
show interface Wireguard0 detail

# Проверить peer статус
show interface Wireguard0 peer detail

# Ping через WireGuard
ping -I Wireguard0 8.8.8.8
```

## Важные замечания

1. **IP-адреса могут меняться** - используйте DNS для получения актуальных адресов
2. **Некоторые сервисы используют CDN** - может потребоваться добавить больше IP-диапазонов
3. **Для доменной маршрутизации** может потребоваться использование dnsmasq или другого DNS-решения
4. **Keenetic может иметь ограничения** на количество статических маршрутов в зависимости от модели

## Альтернативный подход: DNS-based routing

Если статические маршруты не работают эффективно, можно использовать DNS-based routing:

1. Настроить локальный DNS-сервер (dnsmasq)
2. Перенаправлять запросы к определенным доменам через VPN
3. Использовать ipset для автоматического добавления IP-адресов из DNS-ответов
