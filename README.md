# Iconic Storm Watch — backend automático

Serviço que roda continuamente, consulta a NOAA (Storm Prediction Center)
e o NWS a cada X minutos, filtra tempestades dentro de 30 milhas de Kansas
City (KS/MO), mantém o dashboard sempre atualizado e dispara SMS/WhatsApp
para o time quando aparece um relato novo.

⚠️ **Este código não foi testado em produção neste ambiente** (o ambiente
onde ele foi escrito não tem acesso à internet para rodar/testar de
verdade). A lógica segue exatamente a estrutura pública das APIs da NOAA/NWS
e do Twilio, mas antes de contar 100% com ele, faça o teste do passo 6 abaixo.

## O que ele faz

1. A cada `POLL_INTERVAL_MINUTES` (padrão 10 min), busca os relatos do dia
   no SPC (granizo, vento, tornado), filtra para dentro do raio de 30mi de
   Kansas City e nos estados KS/MO.
2. Descobre o ZIP code de cada relato via geocodificação reversa (OpenStreetMap).
3. Compara com o que já foi visto. Se tem relato novo: envia o SMS/WhatsApp
   e atualiza o painel.
4. Serve o dashboard em `/` — ele mesmo se atualiza a cada 60s no navegador,
   sem precisar recarregar a página. É essa URL que você vai colocar no
   iframe do Wix.

## Passo 1 — Criar a conta Twilio

1. Crie uma conta em https://www.twilio.com/try-twilio (tem crédito grátis
   para testar).
2. No Console, copie o **Account SID** e o **Auth Token**.
3. Compre um número de telefone com capacidade de SMS (Console > Phone
   Numbers > Buy a number) — custa poucos dólares por mês + centavos por SMS.
4. (Opcional, para WhatsApp) Para testar rápido, use o **Twilio WhatsApp
   Sandbox** (Console > Messaging > Try it out > Send a WhatsApp message).
   Para enviar WhatsApp em produção pra um número que nunca te mandou
   mensagem antes (que é o seu caso), o Twilio exige um **template de
   mensagem aprovado pela Meta** — esse processo de aprovação pode levar
   alguns dias. Por isso: comece só com SMS (funciona na hora) e adicione
   WhatsApp depois, quando o template estiver aprovado.

## Passo 2 — Configurar as variáveis de ambiente

Copie `.env.example` para `.env` e preencha com os dados do passo 1.
Nunca suba o `.env` real para um repositório público.

## Passo 3 — Rodar localmente (teste rápido)

```bash
pip install -r requirements.txt
export $(cat .env | xargs)   # carrega as variáveis no terminal
python app.py
```

Abra `http://localhost:10000` — deve aparecer o dashboard. Para forçar uma
checagem sem esperar os 10 minutos:

```bash
curl -X POST http://localhost:10000/api/poll-now
```

## Passo 4 — Hospedar (Render, recomendado)

1. Suba esta pasta para um repositório no GitHub.
2. Em https://render.com, clique em "New Web Service", conecte o repositório.
   O `render.yaml` incluído já configura tudo — Render vai pedir pra você
   preencher as variáveis marcadas `sync: false` (as sensíveis) direto na
   interface dele.
3. **Importante:** use o plano "Starter" (pago, ~US$7/mês) ou superior —
   não o Free. O plano Free "dorme" depois de inatividade, o que mataria o
   agendador que faz as checagens automáticas.
4. Depois do deploy, você recebe uma URL fixa tipo
   `https://iconic-storm-watch.onrender.com` — atualize a variável
   `DASHBOARD_URL` com essa URL (ela entra no texto do SMS/WhatsApp).

Alternativas ao Render: Railway, Fly.io ou um VPS simples (DigitalOcean,
Linode) — a lógica é a mesma, só muda o processo de deploy.

## Passo 5 — Embutir no Wix

Na página do Wix (a que você deixou fora do menu / protegida por membros,
como combinamos), adicione o elemento **Embed & Social > HTML iframe**,
escolha a opção **"URL"** (não "Code") e cole a URL do Passo 4. Pronto — o
Wix só exibe uma janela para o serviço que está sempre rodando e sempre
atual.

## Passo 6 — Testar de ponta a ponta

Antes de confiar 100% no serviço, force uma tempestade "de mentira" para
confirmar que o SMS chega: edite temporariamente `storm_state.json` (apague
um `id` da lista `seen_ids`) e rode `poll-now` de novo, ou simplesmente
espere a próxima tempestade real na região — o Kansas/Missouri tem temporada
de granizo forte na primavera e início do verão, então não deve demorar
para um teste real acontecer.

## Estrutura dos arquivos

```
app.py            → serviço principal (agendador + dashboard + API)
storm_data.py      → busca e filtra dados da NOAA/SPC/NWS
notifier.py        → envio de SMS/WhatsApp via Twilio
templates/
  dashboard.html    → painel visual, auto-atualizável
requirements.txt   → dependências Python
render.yaml         → configuração de deploy no Render
.env.example        → modelo de variáveis de ambiente
```
