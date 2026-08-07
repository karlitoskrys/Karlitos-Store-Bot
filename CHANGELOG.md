# Histórico de versões

## v2.3.0 — Painéis de regras

### Adicionado
- Comando `/regras_servidor` exclusivo para o proprietário.
- Comando `/regras_compra` exclusivo para o proprietário.
- Banner próprio para as regras gerais do servidor.
- Banner próprio para as regras de compra.
- Embeds completas com regras em texto para facilitar leitura no Discord.
- Caminhos configuráveis `RULES_SERVER_BANNER_PATH` e `RULES_PURCHASE_BANNER_PATH`.

# Changelog

## v2.2.1 — Ticket Premium

### Corrigido
- O username de **Um nome para tudo** agora aparece publicamente no ticket.
- O canal do ticket é localizado pelo banco de dados, inclusive em interações de modal.

### Melhorado
- Resumo público com username, Display Name, cliente, pedido e distribuição.
- Botões de confirmar e alterar agora usam emojis personalizados do servidor.
- Botões da área de destinatários receberam emojis personalizados.
- Textos e rodapés foram padronizados com a identidade da Karlitos Store.
- Removidas referências a digitar CONFIRMO.

## v2.2.0 — Destinatários simplificados

### Alterado
- Removido o campo obrigatório para digitar `CONFIRMO`.
- O fluxo de divisão agora guia o cliente em 3 etapas.
- Após cada nome, o bot mostra progresso, restante e o próximo seletor.
- Quando a distribuição termina, aparece um resumo com os botões **Confirmar destinatários** e **Editar nomes**.
- Removido o botão **Concluir divisão** do painel novo.
- Mantida compatibilidade com botões antigos já enviados.
- Os nomes continuam bloqueados depois da confirmação final.

# Histórico de versões

## v2.1.0 — 06/08/2026

### Adicionado
- Comando `/pago` para confirmar pagamentos manualmente.
- Comando `/entregue` para concluir pedidos e iniciar o fechamento automático.
- Comando `/fechar` para encerrar tickets manualmente.
- Comando `/pix` para mostrar a chave PIX manual configurada no `.env`.
- Comando `/pedido` para consultar o pedido atual ou um número informado.
- Comando `/comprovante` para solicitar o comprovante ao cliente.
- Comando `/cancelar` para cancelar o pedido do ticket atual.
- Comando `/reabrir` para criar novamente um ticket de pedido fechado.
- Arquivo `VERSION` para controle da versão instalada.

### Alterado
- Os controles administrativos do ticket passam a ser feitos por comandos slash.
- Comandos administrativos são limitados ao `OWNER_USER_ID`.
- Configuração de PIX manual adicionada ao `.env.example`.

### Mantido
- Mercado Pago automático.
- Taxa de processamento.
- Conferência manual a partir do limite configurado.
- Carrinho, cupons, estoque, destinatários, entregas, avaliações e backups.
- Menu personalizado com GIF e emojis do servidor.
