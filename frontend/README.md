# LiraZ Tools — Frontend

Interface gráfica do app desktop. Web app local que roda em http://localhost:5173
durante desenvolvimento, consumindo a API do backend em http://localhost:8000.

## Stack

- **Vite 5** — bundler / dev server
- **React 18** + **TypeScript** (strict mode)
- **Tailwind CSS** + **shadcn/ui** — design system Notion/Stripe
- **TanStack Query** — estado server-side
- **Zustand** — estado client-side
- **React Router** — navegação
- **react-hook-form** + **zod** — formulários

## Setup local

### Pré-requisitos
- Node.js 20+ (você está usando 24, perfeito)
- pnpm 9+ (você está usando 11, perfeito)

### Instalação

```powershell
cd frontend
pnpm install
```

### Rodar em dev

**O backend precisa estar rodando primeiro** em http://localhost:8000.

```powershell
pnpm dev
```

Abre http://localhost:5173 no navegador.

### Comandos úteis

```powershell
# Build de produção
pnpm build

# Type check (sem build)
pnpm typecheck

# Lint
pnpm lint

# Auto-format
pnpm format

# Gerar types da OpenAPI (backend precisa estar rodando)
pnpm generate:api
```

## Estrutura

```
frontend/
├── public/                  # assets estáticos
├── src/
│   ├── api/                 # camada HTTP (client + endpoints tipados)
│   ├── components/
│   │   ├── ui/              # shadcn/ui components (Button, Card, Badge...)
│   │   └── layout/          # AppLayout (header, sidebar...)
│   ├── features/
│   │   └── profiles/        # tudo de perfis: hooks, componentes feature
│   ├── hooks/               # hooks compartilhados
│   ├── lib/                 # utils (cn, queryClient)
│   ├── pages/               # uma página por rota
│   ├── routes/              # config do React Router
│   ├── styles/              # CSS globais
│   ├── types/               # tipos TS espelhando schemas Pydantic
│   ├── App.tsx              # root component
│   └── main.tsx             # entry point
├── biome.json
├── package.json
├── tailwind.config.ts
├── tsconfig.json
└── vite.config.ts
```

## Decisões de design

**Por que TanStack Query?** Substituiu Redux pra 80% dos casos. Cache automático,
refetch em background, devtools. Mutations com invalidação de cache trivial.

**Por que Biome em vez de ESLint + Prettier?** 1 binário, 100x mais rápido.

**Por que shadcn/ui em vez de MUI / Chakra?** shadcn não é biblioteca — é código
que você copia pro projeto e modifica à vontade. Sem update breaking change,
sem bundle inflado, controle total.

**Por que páginas em vez de feature-folders por rota?** Pra app pequeno é mais
claro `pages/X.tsx` que `features/X/pages/index.tsx`. Conforme cresce, migra
páginas pra dentro de features (já temos `features/profiles/`).
