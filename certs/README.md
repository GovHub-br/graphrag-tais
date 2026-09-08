# Certificado do Trino

O `trino-mcp` valida o certificado do Trino **sempre** — `TRINO_SSL_INSECURE`
não funciona nele (o `custom_client` que ele registra no driver Go faz o
trino-go-client ignorar as opções de TLS do DSN; ver ADR-0017). Em homologação o
certificado é self-signed e emitido para o nome do serviço no cluster, **sem IP
SAN**, então são necessárias duas coisas — as duas ficam no `.env`:

1. **confiar no certificado** (`TRINO_CA_FILE` → vira `SSL_CERT_FILE` no container);
2. **falar com ele pelo NOME do certificado** (`TRINO_HOST` = o CN/SAN, e
   `TRINO_TLS_HOSTNAME`/`TRINO_ADDR` mapeando esse nome para o IP real).

Para buscar o PEM do servidor (na VPN):

```bash
echo | openssl s_client -connect <ip>:<porta> -showcerts 2>/dev/null \
  | openssl x509 -outform PEM > certs/trino.crt
```

E para ver por quais nomes ele é válido:

```bash
openssl x509 -in certs/trino.crt -noout -subject -dates -ext subjectAltName
```

Os `.crt` daqui são **gitignored**: são material do ambiente e rotacionam, como
o `.env`. Com CA de verdade em produção, deixe `TRINO_CA_FILE` vazio — aí valem
as CAs públicas da imagem.
