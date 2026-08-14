# Deploy no Render

Guia mínimo pra subir o LiraZ Tools no Render via Blueprint.

## 1. Criar o serviço

1. Vai em https://dashboard.render.com → **New +** → **Blueprint**.
2. Conecta o repo `OmTsTM/liraz-tools-app`, branch `main` (ou outra).
3. Render lê `render.yaml` e mostra: 1 web service + 1 disco de 1GB.
4. **NÃO** clica "Apply" ainda — primeiro precisa gerar os secrets.

## 2. Gerar os 4 secrets (uma vez só, NUNCA reusar)

Roda no PowerShell ou Python qualquer:

```python
# 1) AUTH_SESSION_SECRET — assinatura HMAC do cookie de login
python -c "import secrets; print(secrets.token_urlsafe(48))"

# 2) MASTER_KEY — Fernet pra criptografar credenciais ML
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# 3) BASIC_AUTH_USER — escolhe um, ex.: 'piloto'

# 4) BASIC_AUTH_PASSWORD — gera forte
python -c "import secrets; print(secrets.token_urlsafe(24))"
```

**Importantíssimo**:
- Salva esses valores num lugar seguro (1Password, Bitwarden).
- **Perder a `MASTER_KEY`** = todas as credenciais ML criptografadas viram lixo
  e cada loja precisa refazer OAuth do zero. Mas dados de pedidos / cálculos
  não são afetados, só os tokens criptografados.
- Trocar a `AUTH_SESSION_SECRET` invalida todos os cookies de sessão
  existentes — usuários precisam logar de novo.

## 3. Colar os secrets no dashboard

Na tela do Blueprint, vai aparecer 4 campos pedindo valor (os com
`sync: false`):

| Campo                              | Conteúdo                           |
|------------------------------------|------------------------------------|
| `LIRAZ_TOOLS_AUTH_SESSION_SECRET`  | saída do passo 1                   |
| `LIRAZ_TOOLS_MASTER_KEY`           | saída do passo 2                   |
| `LIRAZ_TOOLS_BASIC_AUTH_USER`      | passo 3 (`piloto`)                 |
| `LIRAZ_TOOLS_BASIC_AUTH_PASSWORD`  | saída do passo 4                   |

Clica **Apply**. Render builda a imagem (~5min na primeira vez).

## 4. Criar o primeiro admin no Render

A `create_admin` precisa rodar dentro do container, então use o **Shell** do
Render (aba "Shell" do service):

```bash
uv run --project backend python -m liraz_tools.scripts.create_admin \
  --email seu-admin@empresa.com \
  --senha "alguma-senha-forte-de-12-chars-ou-mais" \
  --nome "Seu Nome"
```

Depois disso, abre a URL do app (algo tipo `https://liraz-tools.onrender.com`):

1. Browser pede o **Basic Auth** (modal nativo) → entra com user/pass do Render.
2. Aparece a tela de **login do app** → entra com o admin que você acabou
   de criar.
3. Vai em **Avatar → Gerenciar usuários** pra cadastrar o resto da equipe e
   liberar lojas (ACL).

## 5. Verificar que tudo subiu

Test rápido pelo terminal local:

```bash
# Health (BasicAuth liberado pra esse endpoint, monitor do Render usa)
curl -sf https://liraz-tools.onrender.com/health

# Sem Basic Auth: 401 + WWW-Authenticate
curl -s -o /dev/null -w "%{http_code}\n" https://liraz-tools.onrender.com/

# Com Basic Auth + sem login do app: 401 da API
curl -u piloto:SENHA -s -o /dev/null -w "%{http_code}\n" \
  https://liraz-tools.onrender.com/api/profiles
```

## 6. Operação contínua

- **Deploy novo**: `git push origin main` (Render faz auto-deploy se
  `autoDeploy: true`).
- **Backup**: o disco `/data` segura `liraz.db` + arquivos por perfil + a
  master key cache local. Render snapshota o disco; pra backup off-site, no
  shell do Render rode `sqlite3 /data/liraz.db ".backup /data/liraz.db.bkp"`
  e baixa via "Files" do service.
- **Rotacionar BASIC_AUTH**: muda no dashboard → o app reinicia → o navegador
  pede a senha de novo no próximo refresh.
- **Desligar BASIC_AUTH**: deixa os dois env vars vazios (ou apaga). O app
  continua exigindo login normal.

## Erros comuns

- **"crypto error: LIRAZ_TOOLS_MASTER_KEY tem formato inválido"**
  → você colou algo que não é uma `Fernet.generate_key()`. Gere de novo.

- **"Failed to bind 0.0.0.0:8000"**
  → outra app no mesmo host (não deveria acontecer no Render). Confere se
  `PORT` está sendo lido (não fixe 8000 fora do Dockerfile).

- **Cookie não persiste em produção**
  → `LIRAZ_TOOLS_AUTH_COOKIE_SECURE` precisa ser `true` E o app precisa estar
  servindo HTTPS (no Render isso é automático).

- **CORS bloqueado**
  → `LIRAZ_TOOLS_CORS_ORIGINS` precisa bater com o host real do app
  (`https://...onrender.com`). Se você adicionar domínio customizado, atualiza.
