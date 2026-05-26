# FlowForge v2

Generación masiva de imágenes con Google Flow — automatizado, multi-cuenta, con dashboard vanilla.

## ¿Qué hace?

FlowForge v2 automatiza la generación de imágenes en [Google Flow](https://labs.google/fx/tools/flow) usando una extensión de Chrome + un bridge local en Python. Subís tus prompts, elegís las cuentas, y FlowForge genera todo en paralelo — sin tocar el navegador.

```
┌─────────────────┐     ┌──────────────┐     ┌─────────────┐
│  Chrome (Flow)  │────▶│  Python Bridge│────▶│  Dashboard   │
│  extensión MV3   │◀────│  HTTP/WS/SSE  │◀────│  vanilla     │
└─────────────────┘     └──────────────┘     └─────────────┘
```

## Features

- **Auto-auth** — la extensión envía el token de Flow automáticamente al abrir la página
- **Multi-cuenta** — hasta 3 cuentas generando en paralelo, concurrencia configurable
- **Dashboard vanilla** — HTML + CSS + JS puro, dark theme glassmorphism, sin frameworks ni CDN
- **SSE en vivo** — progreso de generación en tiempo real
- **Gestión de proyectos** — auto-detección de `canal/`, migración automática de formato antiguo
- **Imagen de referencia** — subí un personaje y Flow mantiene consistencia visual en todas las escenas
- **Reintento inteligente** — fallos por rate-limit o reCAPTCHA se reintentan solos
- **Carga de prompts** — desde textarea, archivo `.txt` o `.json` con campo `image_prompt`

## Requisitos

- **Python 3.10+**
- **Chrome** (o cualquier navegador Chromium)
- **FFmpeg** (para futura transcripción de audio)

```bash
pip install -r requirements.txt
```

## Instalación

### 1. Clonar el repo

```bash
git clone https://github.com/Wilpoymu/FlowForge-v2.git
cd FlowForge-v2
```

### 2. Configurar variables de entorno

```bash
copy .env.example .env
# Editar .env con tus claves de Flow
```

`.env`:
```
FLOW_API_KEY=...
RECAPTCHA_SITE_KEY=...
```

### 3. Instalar la extensión en Chrome

1. Abrí `chrome://extensions/`
2. Activá **Modo desarrollador**
3. Clic en **Cargar descomprimida**
4. Seleccioná la carpeta `extension/`

### 4. Iniciar el bridge

```bash
python run_bridge.py
```

O en Windows:
```bash
start_bridge.bat
```

### 5. Abrir el dashboard

```
http://127.0.0.1:5556
```

## Uso

1. **Abrí Flow** en Chrome (`labs.google/fx/tools/flow`) — la extensión envía el token solo
2. **El dashboard** muestra las cuentas conectadas automáticamente
3. **Escribí o cargá tus prompts** (uno por línea)
4. **Seleccioná carpeta de salida** y proyecto
5. **Opcional: subí una imagen de referencia** (personaje)
6. **Ajustá concurrencia** (1-20)
7. **Clic en Generar** — mirá el progreso en vivo por SSE

### Imagen de referencia

Para mantener consistencia visual entre escenas:
- Subí una imagen PNG/JPG/WEBP desde el dashboard
- O guardala en `canal/{proyecto}/personaje/` — se carga automáticamente
- Flow usa la referencia en todas las imágenes del batch

## Arquitectura

```
engine/flow_client/
├── __init__.py      # API pública (35 líneas)
├── _core.py         # Constantes, utilidades, estado compartido (114 líneas)
├── _auth.py         # Auto-auth tokens (25 líneas)
├── _projects.py     # CRUD de proyectos + escaneo personaje/ (243 líneas)
├── _generation.py   # FlowClientInstance + batch_generate (456 líneas)
└── _bridge.py       # HTTP/WS/SSE servers + endpoints (740 líneas)

extension/
├── manifest.json    # Chrome MV3
├── flow_token_gen.js # Auto-auth POST + heartbeat
└── flow_content.js  # WebSocket bridge (ISOLATED world)

dashboard/
├── index.html       # UI dark theme glassmorphism (~930 líneas)
└── app.js           # Lógica vanilla JS (~795 líneas)
```

## API Endpoints

| Método | Ruta | Descripción |
|--------|------|-------------|
| `POST` | `/api/auth/auto` | Auto-auth token desde la extensión |
| `POST` | `/api/generate` | Iniciar batch de generación |
| `GET` | `/api/accounts` | Listar cuentas conectadas |
| `GET` | `/api/events?batch={id}` | SSE stream de progreso |
| `GET` | `/api/projects` | Listar proyectos |
| `POST` | `/api/projects` | Crear proyecto |
| `PUT` | `/api/projects` | Actualizar proyecto |
| `GET` | `/api/projects/{name}` | Detalle de proyecto |
| `GET` | `/api/projects/{name}/references` | Imágenes de referencia del proyecto |
| `GET` | `/api/read-file?path=...` | Leer archivo del proyecto |
| `POST` | `/api/projects/migrate` | Migrar proyectos viejos |
| `GET` | `/` | Dashboard HTML |
| `GET` | `/dashboard/*` | Archivos estáticos |

## Rama de desarrollo

El desarrollo activo está en `dev`. `main` contiene la versión estable.

```bash
git checkout dev
```

## Licencia

MIT
