# Guião da demo — Authentik como substituto do Cognito

Para ter aberto num segundo ecrã durante a apresentação. **Orçamento: 12 minutos** mais
perguntas. O detalhe todo está no [README.md](README.md) — esta demo aponta para lá, não o
repete.

A ordem é deliberada: contexto e conclusão primeiro, para toda a audiência; depois a demo; só
no fim a profundidade técnica, para quem vai operar isto. A parte do Argo CD fica para outro
dia.

---

## O run sheet

| Min | Bloco | O que faço | A frase |
|---|---|---|---|
| 0:00 | **Business** | sem ecrã | "Isto é o que a PoC prova sobre sair do Cognito." |
| 0:30 | | | "O código que valida tokens não é específico de nenhum provider. O que muda é o URL do issuer." |
| 1:20 | | | "A pergunta que falta responder: os Identity Pools não têm equivalente." |
| 2:00 | **Demo: admin** | perfil A, `localhost:9100` → Sign in | "Authorization Code com PKCE. O browser não guarda secret nenhum." |
| 2:40 | | cartão *Identity* | "`synergy-admins`, e a app traduz isso em `admin`." |
| 3:00 | | `GET /api/data` → 200, `POST /api/admin` → 200 | — |
| 3:30 | **Demo: signout** | Sign out → Sign in, **parar no login** | "Termina a sessão na app e no Authentik." |
| 4:10 | **Demo: readonly** | perfil B | "Mesmo login, grupo diferente." |
| 4:40 | | `GET /api/data` → 200 | — |
| 5:10 | | `POST /api/admin` → **403** | "O botão estar visível é cortesia, não controlo. Quem decide é a API." |
| 6:00 | **Demo: sem grupo** | perfil C, Sign in na app | "Recusado no IdP. Não foi emitido código nenhum — a app nunca vê um token." |
| 7:00 | **Técnica** | `/if/admin/` → Groups | "Não se escolhe permissão nenhuma. Só pertença." |
| 7:45 | | Providers + Applications | "Provider é o protocolo. Application é a casca, e é onde vive o acesso." |
| 8:45 | | Scope Mappings → `profile` | "O claim `groups` vem do `profile`. Não há mapping à medida." |
| 9:15 | | — | "E é por isso que não usamos Roles." |
| 10:00 | **Configs** | editor | "São dois sítios, e quatro valores que têm de bater certo." |
| 11:00 | | | Cognito: `cognito:groups`, Lambda authorizer, APIs de admin |
| 11:40 | **Fecho** | | duas limitações, próximos passos |
| 12:00 | **Perguntas** | | |

---

## §1 — Business (2 min, falado)

**Porquê estamos aqui.** Queremos sair do Cognito. A PoC tinha de responder a uma pergunta:
podemos autenticar as nossas aplicações contra o Authentik, com dois níveis de acesso, sem
reescrever a forma como as aplicações autorizam?

**A conclusão, e digo-a já.** Sim, e a parte importante é *porque*: o código que valida tokens
não é específico do Cognito nem do Authentik. É OIDC padrão — recebe o bearer token, verifica a
assinatura contra as chaves públicas do issuer, e autoriza a partir de um claim. O que muda na
migração é **o URL do issuer**. Está escrito no próprio resource server da PoC, em
[configmap-api.yaml:9-24](authentik-demo/templates/configmap-api.yaml#L9-L24). É uma mudança de
configuração, não de arquitectura.

**A pergunta aberta, e não a escondo para o fim.** O Cognito são dois produtos. O **User Pool**
tem substituição limpa no Authentik. O **Identity Pool** — que troca um token por credenciais
IAM temporárias — **não tem equivalente nenhum**, e o Authentik não tem sequer o conceito. Se as
páginas a migrar chamam serviços AWS directamente do browser, esse caminho tem de ser
redesenhado. A mitigação conhecida é boa: os Identity Pools aceitam qualquer provider OIDC, logo
substitui-se só o User Pool e mantém-se o Identity Pool a federar para o Authentik. Mas confirmar
se as duas páginas precisam disso exige o código, e é a decisão com mais peso na migração.
Detalhe em [README.md §11](README.md).

---

## §2 — Demo ao vivo (5 min)

### 1. Admin (perfil A, ~90s)

`http://localhost:9100/` → **Sign in with authentik** → `synergy.admin` + password.

O login é o `default-authentication-flow` do Authentik: identificação, depois password. Substitui
a Hosted UI do Cognito e, ao contrário dela, é editável stage a stage.

De volta na app, apontar ao cartão **Identity**:

- `Groups: synergy-admins` — é isto que o Authentik afirma sobre a pessoa
- `Role here: admin` — é a *app* que traduz o grupo em permissão
- `Issuer` — de onde veio o token

Depois, nas chamadas à API: `GET /api/me` (ecoa os claims), `GET /api/data` → **200**,
`POST /api/admin` → **200**.

Se perguntarem pelo PKCE: o endpoint de token recalcula o `SHA256(code_verifier)` e exige que dê
o challenge enviado no início. É isso que substitui o client secret que um browser não consegue
guardar.

### 2. Signout (~40s)

**Sign out** → **Sign in with authentik** → e **paro no ecrã de login**. É a prova: volta a pedir
credenciais.

Vale dizer porque é que isto merece um beat. O default do Authentik para logout iniciado numa
aplicação (`default-provider-invalidation-flow`) termina só a sessão *daquela* aplicação e deixa a
sessão do Authentik viva — correcto em SSO, errado aqui. Apontámos o provider ao
`default-invalidation-flow`, que tem o stage `user_logout`.

Se houver segundos a mais: com o separador `/if/user/` aberto, um refresh depois do signout manda
para o login. Prova que a sessão do IdP também morreu, e não só a da app.

### 3. Readonly (perfil B, ~2 min) — o beat mais importante

Mesmo login, grupo diferente: `Role here: readonly`.

- `GET /api/data` → **200**. Lê.
- `POST /api/admin` → **403**, e o corpo diz exactamente porquê: `required_any_of` e
  `groups_on_token`.

**A frase que justifica a PoC inteira:** o botão estar visível ou escondido é cortesia, não
controlo. A SPA deixa este botão activo de propósito
([configmap-spa.yaml:200](authentik-demo/templates/configmap-spa.yaml#L200)) para se poder clicar
ao vivo. Quem decide é a API, e qualquer pessoa pode chamar o endpoint com `curl`.

> Detalhe a não tropeçar: a dica no ecrã diz *"try it with curl"*, texto que ficou de quando o
> botão estava desactivado. O botão **está activo** — clicar basta. Ignorar a frase ou dizer "o
> texto está desactualizado, o botão funciona".

### 4. Sem grupo (perfil C, ~45s)

Perfil C já está autenticado no Authentik como `akadmin`. Clicar **Sign in with authentik** na app
→ o Authentik usa a sessão existente e responde **`Permission denied`**.

Dois pontos:

- **Não foi emitido código nenhum.** A app nunca vê um token. Isto é negado no IdP, antes da
  aplicação entrar na conversa.
- O `akadmin` é administrador do *Authentik* e não pertence a nenhum dos dois grupos, logo nenhum
  `PolicyBinding` casa. Em `/if/user/` ele nem vê a Synergy Demo na lista.

São os dois sítios onde o acesso pode ser negado: **(a)** no IdP, pelas policies da Application, e
**(b)** na aplicação, pelo claim `groups`. Os dois são necessários — (a) mantém fora quem não é
utilizador, (b) distingue admin de readonly entre quem entrou.

---

## §3 — Técnica (3 min, perfil C, separadores já abertos)

Quatro paragens em `/if/admin/`. Não é um tour — cada uma responde a uma pergunta concreta.

### 1. Directory → Groups

Os dois grupos vieram do blueprint, não foram criados à mão. Abrir o `synergy-admins`: **não há
nenhuma permissão para escolher. Só pertença.** É a totalidade do que o Authentik afirma sobre uma
pessoa.

Como se cria um utilizador, em 15 segundos e sem o fazer agora:

1. *Users → Create* — username, nome, email. **Não há campo de password aqui.**
2. Abrir o utilizador → menu de acções → *Set password*.
3. *Groups → `synergy-admins` → separador Users → Add existing user*.

E a nota que importa numa revisão de segurança: a pertença é avaliada **durante a autenticação** e
fica gravada no token. Tirar alguém de um grupo **não revoga** um token já emitido — mantém o
nível até expirar ou até novo login.

### 2. Providers → `synergy-demo`, e Applications → Synergy Demo

A separação confunde todos à primeira, e justifica-se:

- **O Provider é a maquinaria do protocolo.** `client_type: public`, sem secret, os
  `grant_types`, e os redirect URIs — onde o mesmo URL está registado **duas vezes**, com
  `redirect_uri_type` `authorization` e `logout`. É a segunda entrada que faz o logout funcionar.
- **A Application é a casca visível, e é onde vive o controlo de acesso.** No separador de
  policies estão os dois `PolicyBinding`, com `policy_engine_mode: any` — pertencer a *qualquer*
  dos grupos dá acesso. O `slug` da Application define o issuer OIDC, logo mudá-lo parte todos os
  clientes.

O Cognito não tem este segundo objecto: os App Clients não têm casca visível. É um conceito
próprio do Authentik, e é o que permite trocar o Provider de OIDC para SAML sem os utilizadores
notarem.

### 3. Customization → Property Mappings → Scope Mappings → `profile`

**Não existe um scope `groups` no Authentik.** O claim `groups` é emitido pelo mapping do
`profile`, que vem de origem:

```python
"groups": [group.name for group in request.user.groups.all()],
```

Pedir o scope `profile` é portanto o que torna a autorização por grupo possível, e **não é preciso
property mapping nenhum à medida**. Tirar o `profile` dos scopes faz todos os utilizadores perderem
a role em silêncio — as chamadas passam a devolver 403 sem indicação de porquê.

É o equivalente directo do `cognito:groups`, e existe sem configurar nada.

### 4. Porque não aplicámos Roles

O Authentik tem três camadas de autorização, e só usamos duas:

| Camada | Controla | Usamos |
|---|---|---|
| 1. Pertença a grupo | quem pertence a quê | **sim**, por blueprint |
| 2. RBAC do Authentik — `Role`, `is_superuser` | permissões sobre **o próprio Authentik** | não |
| 3. Autorização da aplicação | o que se pode fazer **dentro da app** | **sim**, na API da PoC |

A camada 2 é a que tem o nome enganador. **Uma Role do Authentik dá permissões sobre o Authentik**,
não sobre as nossas aplicações. Serviria para, por exemplo, um grupo de helpdesk que pudesse fazer
reset de passwords sem ser administrador total. Não serve para distinguir admin de readonly dentro
de uma aplicação.

A frase que resume: **o Authentik nunca sabe o que "admin" significa na nossa aplicação.** Só
afirma pertença a grupo; quem consome é que decide quanto vale essa pertença. É autorização
federada normal, e é exactamente a forma do `cognito:groups` mais um mapeamento do lado da app.

É também por isso que o blueprint diz `is_superuser: false` explicitamente nos dois grupos — é um
campo do modelo, algo tem de ser dito sobre ele, e dizê-lo documenta que o assunto foi considerado.

---

## §4 — Configs, Cognito e próximos passos (2 min)

### Onde está a configuração que liga a webapp ao Authentik

São dois lados, e nenhum deles é clicado numa UI:

| Ficheiro | O que define |
|---|---|
| [management/authentik/values.yaml:26-92](../management/authentik/values.yaml#L26-L92) | lado do IdP: provider, application, slug, redirect URIs, flows, grupos |
| [demo-blueprint-configmap.yaml](../management/authentik/templates/demo-blueprint-configmap.yaml) | o blueprint que cria os 6 objectos no Authentik |
| [authentik-demo/values.yaml:9-55](authentik-demo/values.yaml#L9-L55) | lado da app: issuer, jwksUrl, clientId, scopes, mapa `roles` |
| [configmap-spa.yaml:149-162](authentik-demo/templates/configmap-spa.yaml#L149-L162) | o `UserManager`: PKCE, redirect e post-logout URIs, sem secret |
| [configmap-api.yaml:33-54](authentik-demo/templates/configmap-api.yaml#L33-L54) | o resource server: `ISSUER` compara, `JWKS_URL` busca |

**Quatro valores têm de coincidir entre os dois charts ou o login parte** — está documentado em
`management/authentik/values.yaml:14-25`: `clientId`, `slug`, `redirectUris` e os nomes dos grupos.
Uma discrepância aparece como `redirect_uri_mismatch`, erro de validação do issuer, ou um 403 em
silêncio para todos.

E a parte que merece ser dita em voz alta: tudo isto é **configuração declarativa versionada**. Os
objectos no Authentik que acabámos de ver no admin não foram criados a clicar — são um blueprint em
YAML no repo, aplicado numa transacção. É melhor para GitOps do que as APIs de admin do Cognito.

### O que substitui o Cognito

Os três pares que interessam:

| Cognito | Authentik |
|---|---|
| claim `cognito:groups` | claim `groups` do scope `profile` — existe de origem |
| Lambda authorizer a validar tokens | validação de JWT contra JWKS — mudança de configuração, não de arquitectura |
| APIs de admin (CLI, SDK) | REST API mais blueprints |
| Hosted UI | Flows de Stages, totalmente editáveis |

E o que **não** mapeia: **Identity Pool** (nada), autenticação **SRP** (não suportada),
**multi-pool** (uma instância é um directório; isola-se por Brand). Tabela completa em
[README.md §11](README.md).

### Duas limitações que mudam decisões, e o que vem a seguir

1. **O claim leva nomes de grupo, não IDs estáveis.** Renomear um grupo no Authentik parte todos os
   consumidores que mapeiam nele, sem erro em sítio nenhum. Trata-se nomes de grupo como API, ou
   emite-se UUIDs num mapping próprio.
2. **Isto é self-hosted, logo é nosso para operar.** Backups do PostgreSQL, upgrades e
   disponibilidade passam a ser nosso problema. Em troca: sem custo por utilizador activo, flows
   editáveis, configuração versionada, e providers LDAP, RADIUS, SCIM e Proxy incluídos.

As outras oito estão em [README.md §11](README.md).

**Próximos passos:** deploy em STACKIT — exposição por `HTTPRoute` no Envoy Gateway em vez de
port-forward, hostname real e TLS, secrets por ExternalSecret, PostgreSQL gerido com backups, e
SMTP, que é obrigatório para os flows de recuperação de password. E o Argo CD, noutro dia.

---

# Apêndices

Não consomem tempo de palco. Estão aqui para consulta e para responder a perguntas.

## A — Checklist T-30

- [ ] **Port-forwards num terminal dedicado que não se toca.** Não deixar isto ao k9s: se o k9s
      fechar, o Authentik desaparece e a demo acaba.
      ```bash
      kubectl port-forward --address 0.0.0.0 -n authentik      svc/authentik-server 9000:9000 &
      kubectl port-forward --address 0.0.0.0 -n authentik-demo svc/authentik-demo   9100:8080 &
      ```
- [ ] `curl -s http://localhost:9100/api/health` responde e o `issuer_expected` é o FQDN.
- [ ] `kubectl get pods -n authentik -n authentik-demo` — tudo `Running`.
- [ ] **Entrar no Authentik sempre pelo FQDN**,
      `http://authentik-server.authentik.svc.cluster.local:9000`, nunca por `localhost:9000`. O
      issuer é derivado do header `Host`: os dois hostnames produzem issuers diferentes, e entrar
      pelo errado quebra a validação no browser.
- [ ] **Três perfis de browser, não janelas privadas.** Todas as tabs privadas partilham a mesma
      sessão, e se o `akadmin` estiver autenticado no mesmo perfil, o "Sign in" da app usa essa
      sessão em silêncio e dá `Permission denied`.

| Perfil | Identidade | Estado antes de começar |
|---|---|---|
| **A** | `synergy.admin` | **deslogado**, em `http://localhost:9100/`. É o único login feito ao vivo. |
| **B** | `synergy.viewer` | **já autenticado** na app |
| **C** | `akadmin` | **já autenticado** em `/if/admin/`, com Groups, Providers, Applications e Scope Mappings já abertos em separadores |

- [ ] **Dry run completo e cronometrado**, nesta máquina e nesta rede. É o único passo que apanha
      a ordem errada de beats e um port-forward frágil antes de haver audiência.
- [ ] **Screenshots dos três caminhos** numa pasta: 200 do admin, 403 do readonly,
      `Permission denied`. Se o cluster se portar mal, continua-se a falar em cima das imagens.
- [ ] Notificações e Slack desligados. Separadores a mais fechados.

## B — Perguntas que vão aparecer

**Quanto custa?** Não há custo por utilizador activo, ao contrário do Cognito. O custo passa a ser
operacional: o nosso tempo, o PostgreSQL e a disponibilidade.

**MFA?** Suportado: TOTP, WebAuthn, SMS e Duo, como Stages compostos num flow. Não foi montado na
PoC.

**E se o Authentik estiver em baixo?** Ninguém faz login novo. Tokens já emitidos continuam válidos
até expirar, porque a API valida a assinatura contra as chaves em cache e não chama o IdP a cada
pedido. Em STACKIT isto quer dizer réplicas e um PDB.

**A revogação é imediata?** Não. Alterações de grupo só valem no próximo login, e um token já
emitido mantém o nível até expirar. A mitigação é tempos de vida de token curtos. É o mesmo no
Cognito.

**Como migramos os utilizadores que já existem?** **O Cognito não exporta hashes de password.** Ou
os utilizadores fazem reset, ou migram de forma lazy com um Lambda de user-migration que valida
contra o Cognito uma vez e escreve no Authentik.

**Porque não Keycloak / outro?** Não foi avaliado. A task era avaliar o Authentik.

**Isto corre em produção como está?** Não. Falta exposição real com TLS, secrets a sério em vez de
plaintext, PostgreSQL com backups, monitorização e SMTP. Está listado nos próximos passos.

## C — Se algo falhar

| Sintoma | Causa e acção imediata |
|---|---|
| Uma página deixou de carregar | Um port-forward morreu. **Reiniciar antes de investigar qualquer outra coisa.** |
| O botão de login não faz nada | O port-forward do 9000 caiu — o browser busca o discovery por si. |
| 401 `InvalidIssuerError` | Entrei no Authentik por `localhost` em vez do FQDN. |
| `redirect_uri_mismatch` | O URL não é exactamente `http://localhost:9100/`. `strict` compara caráter a caráter, barra final incluída. |
| `Permission denied` | **É o comportamento correcto.** Dizer isso em voz alta e seguir. |
| 403 para todos, inesperadamente | Falta o scope `profile`, logo o token não tem claim `groups`. |

**Regra:** ao segundo problema técnico, passar aos screenshots e continuar a falar. A audiência
perdoa um cluster local; não perdoa cinco minutos a ver alguém a debugar.

## D — Material cortado, se alguém pedir mais

- **Provar que a autorização não está no browser**, com o token do readonly:
  ```bash
  curl -s -X POST http://localhost:9100/api/admin -H "Authorization: Bearer <token>"
  # 403 — o mesmo que o botão deu, sem browser nenhum
  ```
  O token copia-se do painel *Access token claims* da SPA. Procedimento completo em
  [README.md §10](README.md).
- **Os stages do `default-authentication-flow`**, em *Flows and Stages → Flows*. É o substituto
  editável da Hosted UI; abrir para ver a sequência.
- **Os dois flows de invalidação lado a lado**, para mostrar de onde vinha o problema do logout.
- **A tabela completa de mapeamento Cognito → Authentik**, 20 linhas, em
  [README.md §11](README.md).
- **A sequência completa de login com PKCE**, diagrama em [README.md §7](README.md).
- **`oidc.issuer` vs `oidc.jwksUrl`** e porque são settings separados: [README.md §8](README.md). É
  a pergunta mais provável de quem for fazer o deploy em SKE.
