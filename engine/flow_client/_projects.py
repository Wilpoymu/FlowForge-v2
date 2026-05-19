"""Project management: scan, create, update, list projects."""
from . import _core as _c
from ._core import _log, os, json, datetime, timezone

# ── Project Management ─────────────────────────────────
def _scan_project_folder(proj_dir, name):
    """Scan a project folder and return a normalized project dict."""
    proj = {
        'name': name,
        'title': name.replace('-', ' ').title(),
        'base_dir': proj_dir,
        'files': {'prompts': '', 'audio': '', 'images_dir': 'imagenes', 'thumbnail': '', 'video': ''},
        'stats': {'prompts_total': 0, 'images_generated': 0, 'images_failed': 0},
        'status': 'planning',
        '_name': name,
        '_dir': proj_dir
    }

    # Read existing project.json if present (overrides auto-detected defaults)
    manifest_path = os.path.join(proj_dir, 'project.json')
    if os.path.isfile(manifest_path):
        try:
            with open(manifest_path, 'r', encoding='utf-8') as f:
                saved = json.load(f)
            for key in ('title', 'status', 'created', 'concurrency', 'video_config'):
                if key in saved:
                    proj[key] = saved[key]
            if 'files' in saved:
                proj['files'].update(saved['files'])
            if 'stats' in saved:
                proj['stats'].update(saved['stats'])
        except Exception:
            pass

    if not os.path.isdir(proj_dir):
        return proj

    # Detect files in the folder
    for fname in os.listdir(proj_dir):
        fpath = os.path.join(proj_dir, fname)
        if os.path.isfile(fpath):
            if fname.startswith('prompts-') and fname.endswith('.json'):
                proj['files']['prompts'] = fname
                # Count prompts from the file
                try:
                    with open(fpath, 'r', encoding='utf-8') as f:
                        items = json.load(f)
                    if isinstance(items, list):
                        proj['stats']['prompts_total'] = len(items)
                except Exception:
                    pass
            elif fname.endswith('.mp3') and not proj['files']['audio']:
                proj['files']['audio'] = fname
            elif fname == 'thumbnail.png':
                proj['files']['thumbnail'] = fname
            elif fname.endswith('.mp4') and not proj['files']['video']:
                proj['files']['video'] = fname

    # Detect images directory (imagenes/ or images/)
    for img_dir_name in ('imagenes', 'images'):
        img_dir = os.path.join(proj_dir, img_dir_name)
        if os.path.isdir(img_dir):
            proj['files']['images_dir'] = img_dir_name
            pngs = [f for f in os.listdir(img_dir) if f.endswith('.png')]
            proj['stats']['images_generated'] = len(pngs)
            break

    # Auto-detect stage
    has_prompts = bool(proj['files']['prompts'])
    has_images = proj['stats']['images_generated'] > 0
    has_video = bool(proj['files']['video'])
    if has_video:
        proj['status'] = 'complete'
    elif has_images:
        proj['status'] = 'editing'
    elif has_prompts:
        proj['status'] = 'generating'
    else:
        proj['status'] = 'planning'

    return proj

def _list_projects():
    """Scan base dir for ALL folders (with or without project.json). Auto-detect projects."""
    if not _c._projects_base_dir or not os.path.isdir(_c._projects_base_dir):
        return []
    projects = []
    for name in sorted(os.listdir(_c._projects_base_dir)):
        proj_dir = os.path.join(_c._projects_base_dir, name)
        if not os.path.isdir(proj_dir):
            continue
        manifest_path = os.path.join(proj_dir, 'project.json')
        # Try reading existing project.json
        if os.path.isfile(manifest_path):
            try:
                with open(manifest_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                proj = _scan_project_folder(proj_dir, name)
                # Merge old format fields
                if 'title' in data: proj['title'] = data['title']
                if 'prompts_file' in data and data['prompts_file']:
                    fname = os.path.basename(data['prompts_file'])
                    proj['files']['prompts'] = fname
                    if not proj['stats']['prompts_total']:
                        try:
                            with open(data['prompts_file'], 'r', encoding='utf-8') as pf:
                                items = json.load(pf)
                            if isinstance(items, list):
                                proj['stats']['prompts_total'] = len(items)
                        except Exception:
                            pass
                if 'output_folder' in data:
                    out_dir = os.path.basename(data['output_folder'].rstrip('\\/'))
                    if out_dir in ('imagenes', 'images'):
                        proj['files']['images_dir'] = out_dir
                if 'concurrent_per_account' in data:
                    proj['concurrency'] = data['concurrent_per_account']
                if 'video_config' in data:
                    proj['video_config'] = data['video_config']
                projects.append(proj)
            except Exception:
                projects.append(_scan_project_folder(proj_dir, name))
        else:
            # No project.json — auto-detect from folder contents
            projects.append(_scan_project_folder(proj_dir, name))
    return projects

def _save_project(name, proj):
    """Write project.json to disk (normalized format)."""
    if not _c._projects_base_dir:
        raise ValueError('Projects base directory not configured')
    proj_dir = os.path.join(_c._projects_base_dir, name)
    os.makedirs(proj_dir, exist_ok=True)
    images_dir = os.path.join(proj_dir, proj.get('files', {}).get('images_dir', 'imagenes'))
    os.makedirs(images_dir, exist_ok=True)
    out = {
        'name': name,
        'title': proj.get('title', name),
        'created': proj.get('created', datetime.now(timezone.utc).isoformat()),
        'status': proj.get('status', 'planning'),
        'base_dir': proj_dir,
        'files': proj.get('files', {}),
        'stats': proj.get('stats', {}),
    }
    if 'concurrency' in proj: out['concurrency'] = proj['concurrency']
    if 'video_config' in proj: out['video_config'] = proj['video_config']
    with open(os.path.join(proj_dir, 'project.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    _log(f'Project saved: {name}')
    return out

def _create_project(name, title='', base_dir=None):
    """Create a new project directory with project.json."""
    bd = base_dir or _c._projects_base_dir
    if not bd:
        raise ValueError('Projects base directory not configured')
    safe_name = ''.join(c for c in name if c.isalnum() or c in ' -_').strip().replace(' ', '-').lower()
    if not safe_name:
        raise ValueError('Invalid project name')
    proj_dir = os.path.join(bd, safe_name)
    os.makedirs(proj_dir, exist_ok=True)
    images_dir_name = 'imagenes'
    os.makedirs(os.path.join(proj_dir, images_dir_name), exist_ok=True)
    proj = {
        'name': safe_name,
        'title': title or safe_name,
        'created': datetime.now(timezone.utc).isoformat(),
        'status': 'planning',
        'base_dir': proj_dir,
        'files': {'prompts': '', 'audio': '', 'images_dir': images_dir_name, 'thumbnail': '', 'video': ''},
        'stats': {'prompts_total': 0, 'images_generated': 0, 'images_failed': 0}
    }
    _save_project(safe_name, proj)
    _log(f'Project created: {safe_name} in {proj_dir}')
    proj['_name'] = safe_name
    proj['_dir'] = proj_dir
    return proj

def _get_project(name):
    """Get a project by name (smart scan)."""
    if not _c._projects_base_dir:
        return None
    proj_dir = os.path.join(_c._projects_base_dir, name)
    if not os.path.isdir(proj_dir):
        return None
    return _scan_project_folder(proj_dir, name)

def _update_project(name, updates):
    """Merge updates and save project.json."""
    proj = _get_project(name)
    if not proj:
        return None
    for key in ('files', 'stats', 'status', 'title', 'concurrency', 'video_config'):
        if key in updates:
            if isinstance(updates[key], dict) and isinstance(proj.get(key), dict):
                proj[key].update(updates[key])
            else:
                proj[key] = updates[key]
    return _save_project(name, proj)


_batch_project = {}  # batch_id -> project_name
