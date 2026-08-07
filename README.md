# Karlitos Store Bot v2.3.0

Bot de vendas da Karlitos Store com Discord, Mercado Pago, carrinho, tickets, entrega e administração por comandos slash.

## Instalação

1. Copie para esta pasta o seu `.env` atual.
2. Copie o seu `loja.db` atual.
3. Copie o seu `private_servers.json` atual.
4. Mantenha `karlitos_store_banner_animado.gif` na raiz.
5. No terminal, execute:

```bash
pip install -r requirements.txt
python main.py
```

## Novas configurações no `.env`

```env
MANUAL_PIX_KEY=SUA_CHAVE_PIX
MANUAL_PIX_HOLDER=NOME_DO_TITULAR
```

`MANUAL_PIX_HOLDER` é opcional. O comando `/pix` usa essas informações.

## Comandos de ticket

- `/pago`
- `/entregue`
- `/fechar`
- `/pix`
- `/pedido`
- `/comprovante`
- `/cancelar`
- `/reabrir numero:<ID>`

Os comandos administrativos verificam o `OWNER_USER_ID` configurado no `.env`.

## Segurança

Nunca envie ou publique seu `.env`, token do Discord ou Access Token do Mercado Pago.

## Versão

A versão instalada está registrada no arquivo `VERSION`. Consulte `CHANGELOG.md` para ver as alterações.


## Destinatários simplificados (v2.3.0)

O cliente escolhe **Um nome para tudo** ou **Adicionar vários nomes**. No modo dividido, o bot mostra o progresso e repete o seletor até todo o pedido estar distribuído. Não é mais necessário digitar `CONFIRMO`. Ao final, basta revisar e clicar em **Confirmar destinatários**.


## Painéis de regras

Use no canal desejado:

```text
/regras_servidor
/regras_compra
```

Somente a conta configurada em `OWNER_USER_ID` pode publicar esses painéis. Os banners precisam permanecer na raiz do projeto com estes nomes:

```text
regras_servidor_karlitos.png
regras_compra_karlitos.png
```
