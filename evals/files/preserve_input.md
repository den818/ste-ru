## Как перевыпустить токен интеграции

Для того чтобы перевыпустить токен интеграции с почтовым провайдером, необходимо осуществить следующие действия. Сначала следует выполнить команду `make token-rotate --env prod`, которая обращается к сервису `mail-gateway` и запрашивает новый токен; в случае успеха в логе появится строка «token rotated: id=tok_8f2a, expires_in=86400».

Если же сервис вернул ошибку, в логе будет строка «rotation rejected by provider: reason=RATE_LIMIT», и в таком случае повторную попытку производить можно не ранее чем через 15 минут, о чём необходимо сообщить в тикете PROJ-4521.

Подробное описание протокола находится по адресу https://wiki.example.com/notifications/mail-gateway/token-rotation, а конфигурация клиента лежит в файле /etc/notifications/mail-gateway.yaml.

```bash
make token-rotate --env prod
tail -n 50 /var/log/mail-gateway/rotation.log
```
