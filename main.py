import base64
import datetime
import hashlib
import imaplib
import email as email_lib
import email.header
import json
import os
import random
import re
import sys
import time
import uuid
from collections import deque
from curl_cffi import requests as cr
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
INPUT_DIR = os.path.join(DATA_DIR, 'input')
OUTPUT_DIR = os.path.join(DATA_DIR, 'output')
INVITE_FILE_DEFAULT = os.path.join(INPUT_DIR, 'invite_code.txt')
ACCOUNTS_OUTPUT_DEFAULT = os.path.join(OUTPUT_DIR, 'accounts.txt')
PENDING_SWAP_FILE = os.path.join(OUTPUT_DIR, 'pending_swaps.txt')
FARM_LOCK_FILE = os.path.join(DATA_DIR, '.farm.lock')

def init_data_dirs():
    os.makedirs(INPUT_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

def _count_saved_accounts(path=None, fallback=0):
    try:
        p = path or ACCOUNTS_OUTPUT_DEFAULT
        if not os.path.exists(p):
            return 0
        with open(p, encoding='utf-8') as f:
            return sum(1 for line in f if line.strip())
    except Exception:
        return fallback

def save_account_output(email, code='', dob='', path=None):
    p = path or ACCOUNTS_OUTPUT_DEFAULT
    init_data_dirs()
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
    c_str = f"Code: {code}" if code else "No code"
    d_str = f"DOB: {dob}" if dob else "DOB: random"
    line = f"{email} | {c_str} | {d_str} | {timestamp}\n"
    try:
        with open(p, 'a', encoding='utf-8') as f:
            f.write(line)
        log('output', f'saved account to {p}: {email}')
        try:
            TUI['accounts_saved'] = int(TUI.get('accounts_saved') or 0) + 1
        except Exception:
            pass
        return True
    except Exception as e:
        log('output', f'failed to save account to {p}: {e}')
        return False

def _record_pending_swap(temp_email, default_email, phase='confirmed'):
    """Persist a confirmed swap before the default email is detached.

    If step 3 fails or the process dies in between, this record keeps the
    confirmed temp email recoverable instead of orphaning it on Meta.
    """
    try:
        init_data_dirs()
        ts = time.strftime('%Y-%m-%d %H:%M:%S')
        # Rotate the audit log so it cannot grow without bound (keep last 400 rows).
        if os.path.exists(PENDING_SWAP_FILE):
            try:
                with open(PENDING_SWAP_FILE, encoding='utf-8') as f:
                    rows = f.readlines()
                if len(rows) >= 500:
                    with open(PENDING_SWAP_FILE, 'w', encoding='utf-8') as f:
                        f.writelines(rows[-400:])
            except Exception:
                pass
        with open(PENDING_SWAP_FILE, 'a', encoding='utf-8') as f:
            f.write(f'{ts} | {phase} | {temp_email} | replaces {default_email}\n')
        return True
    except Exception as e:
        log('swap', f'could not record pending swap: {e}')
        return False

def _acquire_farm_lock():
    """Single-instance guard for the auto farm loop."""
    init_data_dirs()
    try:
        fd = os.open(FARM_LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(fd, str(os.getpid()).encode('ascii', errors='ignore'))
        except OSError:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.remove(FARM_LOCK_FILE)
            except OSError:
                pass
            fail('farm', f'cannot write farm lock {FARM_LOCK_FILE}')
        return fd
    except FileExistsError:
        fail('farm', f'another farm instance appears to be running - lock file: {FARM_LOCK_FILE} (delete it only if the previous run crashed)')
    except OSError as e:
        fail('farm', f'cannot create farm lock {FARM_LOCK_FILE}: {e}')

def _release_farm_lock(fd):
    if fd is None:
        return
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        os.remove(FARM_LOCK_FILE)
    except OSError:
        pass

DEFAULT_CONFIG = {'mode': 'signup_redeem', 'ref_code': '', 'email': '', 'first_name': 'Miauru', 'last_name': 'Juanda', 'date_of_birth': '1998-11-12', 'random_profile': True, 'proxy_enabled': False, 'proxy': '', 'vcc': {'number': '', 'expiry': '', 'cvv': '', 'zip': '', 'country': 'US'}, 'product_id': '766358168544755', 'receiver_id': '352501280694489'}

def get_proxies(cfg):
    if cfg.get('proxy_enabled') and cfg.get('proxy'):
        return {'http': cfg['proxy'], 'https': cfg['proxy']}
    return {}
CFG = dict(DEFAULT_CONFIG)
PROXIES = get_proxies(CFG)
FUSION_HOST = 'https://fusionblogcommunity.com'
FUSION_KEY = (os.environ.get('MUSE_FUSION_KEY') or '').strip()
FUSION_BASE = (os.environ.get('MUSE_FUSION_BASE') or '').strip()
FUSION_DOMAIN = 'fusionblogcommunity.com'
MUSE_BASE = 'https://muse.ai'
REGION = 'US'
TOS_VERSION = 'ntm_create_account:default_v1_meta_account_v2+docs_v1'
PTT_APP_ID = '2805739562851096'
SUPPORTED_COUNTRIES = ['US', 'CA', 'GB', 'DZ', 'AR', 'AU', 'AT', 'BE', 'BR', 'CL', 'CO', 'CI', 'HR', 'CZ', 'DK', 'EG', 'FI', 'FR', 'DE', 'GH', 'HK', 'IS', 'IN', 'ID', 'IE', 'IL', 'IT', 'JP', 'KE', 'LB', 'MY', 'MX', 'MA', 'NL', 'NZ', 'NG', 'NO', 'PK', 'PH', 'PL', 'PT', 'QA', 'RO', 'SA', 'SN', 'SG', 'ZA', 'KR', 'ES', 'SE', 'CH', 'TW', 'TH', 'TR', 'AE', 'VN', 'ZM', 'ZW']
COUNTRY_ALIAS = {'USA': 'US', 'UK': 'GB', 'ENGLAND': 'GB', 'AUS': 'AU', 'UAE': 'AE', 'KOREA': 'KR', 'SOUTH KOREA': 'KR'}
FIRST_NAMES = ['Miauru', 'Anya', 'Sinta', 'Putri', 'Dian', 'Nadia', 'Fitri', 'Intan', 'Rani', 'Wulan', 'Lestari', 'Ayu', 'Bima', 'Rizky', 'Fajar', 'Dimas', 'Agus', 'Budi', 'Andi', 'Yoga', 'Reza', 'Ilham', 'Fahmi', 'Galih', 'Bagas', 'Teguh', 'Hendri', 'Nabila', 'Kirana', 'Zahra', 'Amelia', 'Dinda', 'Salsa', 'Ratna', 'Melati', 'Anggun']
LAST_NAMES = ['Juanda', 'Pratama', 'Nugroho', 'Saputra', 'Kusuma', 'Wijaya', 'Santoso', 'Hidayat', 'Ramadhan', 'Firmansyah', 'Setiawan', 'Gunawan', 'Hakim', 'Maulana', 'Syahputra', 'Rahmawati', 'Anggraini', 'Maharani', 'Laksmana', 'Puspita', 'Kartika', 'Handayani', 'Septiani', 'Utami']
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36'

def log(step, msg):
    if TUI.get('active'):
        tui_log(step, msg)
    else:
        print(f'[{step}] {msg}', flush=True)

def fail(step, msg):
    print(f'[{step}] FAILED: {msg}', flush=True)
    try:
        tui_stop()
    except Exception:
        pass
    sys.exit(1)

def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip('=')

def js_json(obj) -> str:
    return json.dumps(obj, separators=(',', ':'))

def random_profile():
    today = time.localtime()
    min_y, max_y = (today.tm_year - 40, today.tm_year - 19)
    dob = f'{random.randint(min_y, max_y):04d}-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}'
    return (random.choice(FIRST_NAMES), random.choice(LAST_NAMES), dob)

def normalize_dob(s: str) -> str:
    val = (s or '').strip().lower()
    if not val or val == 'random':
        return 'random'
    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})$', val)
    if not m:
        raise ValueError("Invalid date format — must be YYYY-MM-DD (e.g. 1998-11-12) or 'random'")
    y, m_val, d_val = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= m_val <= 12 and 1 <= d_val <= 31):
        raise ValueError("Invalid month or day in date of birth")
    try:
        birth = datetime.date(y, m_val, d_val)
    except ValueError:
        raise ValueError("Invalid calendar date in date of birth")
    today = datetime.date.today()
    age = today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))
    if age < 18:
        raise ValueError("Date of birth indicates age < 18 (Meta requires at least 18 years old)")
    if age > 75:
        raise ValueError("Date of birth indicates age > 75")
    return f"{y:04d}-{m_val:02d}-{d_val:02d}"

def _u32be(n: int) -> bytes:
    return n.to_bytes(4, 'big')

def ptt_build_token(trust_chain_b64: str, data: dict, secret_payload: dict | None) -> str:
    inner = {'data': data, 'nonce': str(uuid.uuid4()), 'op': data.pop('_op'), 'ver': 1}
    inner_b64 = b64u(js_json(inner).encode())
    if secret_payload is None:
        payload = inner_b64
        return b64u(js_json({'payload': payload, 'signatures': []}).encode())
    try:
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, load_der_public_key
    except ImportError:
        fail('vcc', 'missing dependency: cryptography - run: python -m pip install cryptography')
    cert_der = base64.b64decode(trust_chain_b64)
    try:
        from cryptography import x509
        cert = x509.load_der_x509_certificate(cert_der)
        spki_der = cert.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    except Exception:
        spki_der = cert_der
    finally:
        pass
    server_pub = load_der_public_key(spki_der)
    if not isinstance(server_pub, ec.EllipticCurvePublicKey):
        fail('ptt', 'server key is not EC')
    eph_priv = ec.generate_private_key(ec.SECP256R1())
    eph_pub = eph_priv.public_key()
    eph_spki = eph_pub.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    Z = eph_priv.exchange(ec.ECDH(), server_pub)
    fp = 'fp:' + b64u(hashlib.sha256(eph_spki).digest())
    kdf_input = b''.join([_u32be(1), Z, _u32be(len('A256GCM')), b'A256GCM', _u32be(0), b'', _u32be(len(fp)), fp.encode(), _u32be(256)])
    aes_key = hashlib.sha256(kdf_input).digest()
    pem_b64 = base64.b64encode(eph_spki).decode()
    pem = '-----BEGIN PUBLIC KEY-----\n'
    for i in range(0, len(pem_b64), 64):
        pem += pem_b64[i:i + 64] + '\n'
    pem += '-----END PUBLIC KEY-----\n'
    protected = {'alg': 'ECDH-ES', 'apu': '', 'apv': b64u(fp.encode()), 'enc': 'A256GCM', 'epk': {'crv': 'P-256', 'kty': 'EC', 'pem': pem}}
    prot_b64 = b64u(js_json(protected).encode())
    aad = (prot_b64 + '.' + inner_b64).encode()
    iv = os.urandom(12)
    ct_tag = AESGCM(aes_key).encrypt(iv, js_json(secret_payload).encode(), aad)
    ct, tag = (ct_tag[:-16], ct_tag[-16:])
    payload = f'{inner_b64}.{prot_b64}..{b64u(iv)}.{b64u(ct)}.{b64u(tag)}'
    return b64u(js_json({'payload': payload, 'signatures': []}).encode())

def ptt_card_token(trust_chain_b64, card_number, cvv, exp_mm, exp_yyyy):
    data = {'app_id': PTT_APP_ID, 'auth_ticket_type': 'PAN', 'cardholder_name': '', 'credit_card': '$e2ee', 'csc': '$e2ee', 'device_id': 'DEVICE_ID', 'expiry_month': exp_mm, 'expiry_year': exp_yyyy, '_op': 'ADD_CARD'}
    secret = {'csc': cvv, 'credit_card': card_number}
    return ptt_build_token(trust_chain_b64, data, secret)

def ptt_container_token(order_id, receiver_id, container_mode, amount, currency, cred_id):
    amt = float(amount)
    if amt.is_integer():
        amt = int(amt)
    data = {'receiver_id': receiver_id, 'context_id': order_id, 'container_mode': container_mode, 'components': [{'amount': amt, 'cred_id': cred_id, 'currency': currency}], 'security_origin': 'https://muse.ai', '_op': 'CREATE_CONTAINER'}
    return ptt_build_token('', data, None)

def new_api_session():
    s = cr.Session(impersonate='chrome', proxies=PROXIES)
    s.headers.update({'User-Agent': UA, 'Accept': '*/*', 'Accept-Language': 'en-US,en;q=0.9', 'Origin': MUSE_BASE, 'Referer': MUSE_BASE + '/', 'x-hatch-caa-reg-entry-point': 'login_home', 'x-hatch-waterfall-id': str(uuid.uuid4()), 'Sec-Fetch-Site': 'same-origin', 'Sec-Fetch-Mode': 'cors', 'Sec-Fetch-Dest': 'empty'})
    return s

def sentry_trace():
    return f'{uuid.uuid4().hex}{uuid.uuid4().hex[:16]}-{uuid.uuid4().hex[:16]}-0'

def api_post(sess, path, payload, csrf=None, tries=3, timeout=30):
    headers = {'sentry-trace': sentry_trace(), 'Content-Type': 'application/json'}
    if csrf:
        headers['x-hatch-csrf-token'] = csrf
    last = None
    for a in range(tries):
        try:
            r = sess.post(MUSE_BASE + path, json=payload, headers=headers, timeout=timeout)
            if r.status_code in (200, 201):
                return r
            if r.status_code == 400 and 'confirm-otp' in path:
                fail('auth', f'Meta rejected OTP ({r.text[:160]})')
            last = f'HTTP {r.status_code}: {r.text[:160]}'
        except Exception as e:
            last = f'{type(e).__name__}: {e}'
        finally:
            pass
        time.sleep(2 * (a + 1))
    fail('http', f'POST {path} failed {tries}x: {last}')

def app_request(sess, method, path, payload=None, referer=MUSE_BASE + '/', tries=3, timeout=30, fatal=True):
    headers = {'Accept': '*/*', 'Referer': referer, 'Origin': MUSE_BASE, 'Content-Type': 'application/json'}
    last = None
    for a in range(tries):
        try:
            if method == 'POST':
                r = sess.post(MUSE_BASE + path, json=payload, headers=headers, timeout=timeout)
            else:
                r = sess.get(MUSE_BASE + path, headers=headers, timeout=timeout)
            if r.status_code == 200:
                return r
            last = f'HTTP {r.status_code}: {r.text[:160]}'
        except Exception as e:
            last = f'{type(e).__name__}: {e}'
        finally:
            pass
        time.sleep(2 * (a + 1))
    if not fatal:
        log('http', f'{method} {path} -> {last} (non-fatal)')
        return None
    fail('http', f'{method} {path} failed {tries}x: {last}')

def app_post(sess, path, payload, referer=MUSE_BASE + '/'):
    return app_request(sess, 'POST', path, payload, referer)

def start_flow():
    api = new_api_session()
    px = mask_proxy(CFG.get('proxy') or '')
    tries = 5
    for i in range(tries):
        try:
            r = api.get(MUSE_BASE + '/', timeout=25)
            log('net', f'landing OK (HTTP {r.status_code})')
            break
        except Exception as e:
            log('net', f"landing GET {i + 1}/{tries} failed via {px or 'direct IP'} ({e})")
            time.sleep(5 * (i + 1))
        finally:
            pass
    else:
        fail('restart', f"landing page unreachable: muse.ai could not be reached after {tries} attempts (proxy: {px or 'direct IP'}). Switch to a US proxy / check VPN, then retry.")
    r = api_post(api, '/api/auth/native/restart', {})
    try:
        csrf = r.json()['csrf_token']
    except (KeyError, ValueError):
        fail('restart', f'no csrf_token: {r.text[:200]}')
    finally:
        pass
    log('restart', 'flow started')
    return (api, csrf)

def send_otp(api, csrf, email):
    r = api_post(api, '/api/auth/native/send-otp', {'contact_point': email, 'region': REGION}, csrf=csrf)
    log('send-otp', f'OTP sent to {email}')

def confirm_otp(api, csrf, otp):
    r = api_post(api, '/api/auth/native/confirm-otp', {'otp_code': otp}, csrf=csrf)
    return r.json()

def register_completion(api, csrf, first, last, dob):
    r = api_post(api, '/api/auth/native/register-completion', {'date_of_birth': dob, 'first_name': first, 'last_name': last, 'tos_content_version': TOS_VERSION}, csrf=csrf)
    return r.json()

def save_account(api):
    r = app_request(api, 'POST', '/api/auth/save-account', {}, referer=MUSE_BASE + '/', fatal=False)
    if r is None:
        log('save-account', 'save-account failed (non-fatal, continuing)')
        return
    try:
        log('save-account', f"saved={r.json().get('saved')}")
    except Exception:
        log('save-account', f'saved response: {r.status_code}')

def bootstrap_session(api, frl_access_token):
    log('bootstrap', 'exchanging token for session...')
    nav_headers = {'Content-Type': 'application/x-www-form-urlencoded', 'Referer': MUSE_BASE + '/', 'Origin': MUSE_BASE, 'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8', 'Accept-Language': 'en-US,en;q=0.9', 'Upgrade-Insecure-Requests': '1', 'Sec-Fetch-Dest': 'document', 'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-User': '?1'}
    r = api.post('https://auth.meta.com/hatch/session/bootstrap/', data={'frl_access_token': frl_access_token}, headers=nav_headers, allow_redirects=False, timeout=30)
    for _ in range(5):
        loc = r.headers.get('location')
        if r.status_code not in (301, 302, 303, 307, 308) or not loc:
            break
        r = api.get(loc, headers={'Referer': MUSE_BASE + '/'}, allow_redirects=False, timeout=30)
    if r.status_code >= 400:
        fail('bootstrap', f'session bootstrap failed (HTTP {r.status_code})')
    if r.status_code in (301, 302, 303, 307, 308):
        fail('bootstrap', 'session bootstrap ended on a redirect without a Location header')
    log('bootstrap', f'active session (final: {r.url[:60]})')

def _mail_sess():
    s = cr.Session(impersonate='chrome', proxies=PROXIES)
    s.headers.update({'User-Agent': UA})
    return s

def _require_fusion():
    missing = [name for name, val in (('MUSE_FUSION_KEY', FUSION_KEY), ('MUSE_FUSION_BASE', FUSION_BASE)) if not val]
    if missing:
        fail('fusion', f'missing environment variable(s): {", ".join(missing)} - set them before using the Fusion mailbox')

def fusion_new_email(tries=3):
    _require_fusion()
    s = _mail_sess()
    last = None
    for a in range(tries):
        try:
            r = s.get(FUSION_HOST + '/api/otp/new', params={'base': FUSION_BASE}, timeout=30)
            data = r.json()
            email = data.get('email')
            if email:
                log('email', f'temp mail Fusion: {email}')
                return email
            last = f'no email returned: {r.text[:160]}'
        except Exception as e:
            last = f'{type(e).__name__}: {e}'
        finally:
            pass
        time.sleep(2 * (a + 1))
    fail('email', f'fusion /api/otp/new failed {tries}x: {last}')

def fusion_wait_otp(email, timeout=45, tries=3):
    _require_fusion()
    s = _mail_sess()
    last = None
    for a in range(tries):
        try:
            r = s.get(FUSION_HOST + '/api/otp/wait', params={'email': email, 'timeout': timeout, 'key': FUSION_KEY}, timeout=timeout + 15)
            data = r.json()
            if data.get('found') and data.get('otp'):
                log('otp', f'OTP received: {data.get("otp")}')
                return str(data['otp'])
            last = f'OTP not ready yet: {str(data)[:160]}'
        except Exception as e:
            last = f'{type(e).__name__}: {e}'
        finally:
            pass
        time.sleep(2 * (a + 1))
    fail('otp', f'fusion /api/otp/wait timed out {tries}x: {last}')

GMAIL_IMAP = 'imap.gmail.com'
GMAIL_META_SENDERS = ('meta.com', 'facebookmail.com', 'facebook.com', 'oculus.com')
SEEN_GMAIL_MSG_IDS = set()

def _is_recent_msg(msg, max_age_seconds=1200):
    date_str = msg.get('Date')
    if not date_str:
        return True
    try:
        import datetime
        msg_dt = email_lib.utils.parsedate_to_datetime(date_str)
        now_dt = datetime.datetime.now(datetime.timezone.utc)
        if msg_dt.tzinfo is None:
            msg_dt = msg_dt.replace(tzinfo=datetime.timezone.utc)
        age = (now_dt - msg_dt).total_seconds()
        return -600 <= age <= max_age_seconds
    except Exception:
        return True

def _is_gmail_address(email):
    e = (email or '').strip().lower()
    return e.endswith('@gmail.com') or e.endswith('@googlemail.com')

def _extract_otp_from_text(text, sender_hint=''):
    body = text or ''
    frm = (sender_hint or '').lower()
    meta_sender = any(d in frm for d in GMAIL_META_SENDERS)
    mm = re.search(r'(?:confirmation code|use code|code is|enter this code|your code|verification code)[^\d]{0,30}(\d{6})', body, re.IGNORECASE)
    if mm:
        return mm.group(1)
    mm = re.search(r'(\d{6})[^\d]{0,20}(?:code|log in|login|verify|confirmation)', body, re.IGNORECASE)
    if mm:
        return mm.group(1)
    if meta_sender:
        # Meta often repeats the code in the same email ("123456 ... 123456")
        mm = re.search(r'\b(\d{6})\b[^\n]{0,80}\b\1\b', body)
        if mm:
            return mm.group(1)
    return None

def _get_fetch_raw_bytes(mdata):
    if not mdata or not isinstance(mdata, list):
        return None
    for item in mdata:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
            return item[1]
    return None

def _decode_mime(value):
    if not value:
        return ''
    out = []
    for chunk, enc in email_lib.header.decode_header(value):
        if isinstance(chunk, bytes):
            try:
                out.append(chunk.decode(enc or 'utf-8', errors='replace'))
            except Exception:
                out.append(chunk.decode('utf-8', errors='replace'))
        else:
            out.append(chunk)
    return ''.join(out)

def _otp_from_msg(msg):
    sender = _decode_mime(msg.get('From') or '')
    subject = _decode_mime(msg.get('Subject') or '')

    # Priority 1: Check Subject line FIRST (100% precision for Meta confirmation emails)
    # Example subjects: "539077 is your Meta confirmation code" or "539077 là mã xác nhận Meta của bạn"
    sub_m = re.search(r'\b(\d{6})\b', subject)
    if sub_m:
        sub_lower = subject.lower()
        otp_kw = any(k in sub_lower for k in ('code', 'mã', 'ma xac nhan', 'confirm', 'xác nhận', 'verification', 'security code', 'otp'))
        meta_ctx = 'meta' in sub_lower or any(d in sender.lower() for d in GMAIL_META_SENDERS)
        if otp_kw and meta_ctx:
            return sub_m.group(1)

    # Priority 2: Extract text from body
    text = ''
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct not in ('text/plain', 'text/html'):
                continue
            try:
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    text += payload.decode('utf-8', errors='replace') + ' '
            except Exception:
                pass
    else:
        try:
            payload = msg.get_payload(decode=True)
            if isinstance(payload, bytes):
                text = payload.decode('utf-8', errors='replace')
        except Exception:
            text = ''

    # Priority 3: Targeted Meta body patterns (ignoring zip codes like 94025 or street numbers)
    clean_text = re.sub(r'<[^>]+>', ' ', text)
    body_pats = (
        r'confirmation\s*code[^\d]{0,25}(\d{6})',
        r'mã\s*xác\s*nhận[^\d]{0,25}(\d{6})',
        r'following\s*confirmation\s*code[^\d]{0,35}(\d{6})',
        r'use\s*code[^\d]{0,25}(\d{6})',
        r'code\s*is[^\d]{0,25}(\d{6})',
        r'security\s*code[^\d]{0,25}(\d{6})',
        r'enter\s*(?:this|the)?\s*code[^\d]{0,25}(\d{6})',
        r'(\d{6})\s*(?:is your|là mã|to confirm|để xác nhận)',
    )
    for pat in body_pats:
        m = re.search(pat, clean_text, re.IGNORECASE)
        if m:
            return m.group(1)

    return _extract_otp_from_text(clean_text + ' ' + subject, sender)

def gmail_snapshot_recent_msg_ids(gmail, app_password):
    """Return Message-IDs already in the inbox, or None when the snapshot failed.

    None is deliberately distinct from an empty set: a partial snapshot could
    let an old OTP through, so callers treat None as "no reliable baseline".
    """
    import ssl
    seen = set()
    M = None
    ok = True
    try:
        M = imaplib.IMAP4_SSL(GMAIL_IMAP, 993, ssl_context=ssl.create_default_context(), timeout=30)
        M.login(gmail, app_password)
        typ, data = M.select('INBOX', readonly=True)
        if typ != 'OK':
            raise imaplib.IMAP4.error(f'SELECT INBOX failed: {typ}')
        typ, data = M.search(None, 'ALL')
        if typ == 'OK' and data and data[0]:
            ids = data[0].split()
            recent_ids = ids[-30:] if len(ids) > 30 else ids
            for i in recent_ids:
                try:
                    i_str = i.decode() if isinstance(i, bytes) else str(i)
                    typ, mdata = M.fetch(i_str, '(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])')
                    raw_bytes = _get_fetch_raw_bytes(mdata)
                    if not raw_bytes:
                        continue
                    header_text = raw_bytes.decode('utf-8', errors='ignore')
                    for line in header_text.splitlines():
                        if line.lower().startswith('message-id:'):
                            m_id = line.split(':', 1)[1].strip()
                            if m_id:
                                seen.add(m_id)
                except Exception:
                    pass
    except Exception as e:
        ok = False
        log('gmail', f'snapshot existing emails error: {e} (recency guard still active)')
    finally:
        if M is not None:
            try:
                M.logout()
            except Exception:
                pass
    return seen if ok else None

def _gmail_connect(gmail, app_password):
    import ssl
    M = imaplib.IMAP4_SSL(GMAIL_IMAP, 993, ssl_context=ssl.create_default_context(), timeout=30)
    try:
        M.login(gmail, app_password)
        typ, _ = M.select('INBOX')
        if typ != 'OK':
            raise imaplib.IMAP4.error(f'SELECT INBOX failed: {typ}')
    except Exception:
        try:
            M.logout()
        except Exception:
            pass
        raise
    return M

def gmail_wait_otp(gmail, app_password, timeout=150, poll=3, ignore_msg_ids=None):
    import ssl
    log('gmail', f'waiting for OTP in Gmail {gmail} ({timeout}s)...')
    deadline = time.time() + timeout
    start_time = time.time()
    ignored = set(ignore_msg_ids) if ignore_msg_ids else set()
    try:
        M = _gmail_connect(gmail, app_password)
    except (imaplib.IMAP4.error, OSError, ssl.SSLError) as e:
        fail('gmail', f'IMAP login failed for {gmail}: {e} — check App Password / ensure IMAP is enabled')
    try:
        while time.time() < deadline:
            try:
                M.noop()
                typ, data = M.search(None, 'ALL')
            except Exception as e:
                # Socket dropped mid-loop (proxy rotation / Gmail idle timeout) — reconnect
                log('gmail', f'IMAP error ({type(e).__name__}); reconnecting...')
                try:
                    M.logout()
                except Exception:
                    pass
                try:
                    M = _gmail_connect(gmail, app_password)
                    log('gmail', 'IMAP reconnected')
                except Exception as e2:
                    log('gmail', f'IMAP reconnect failed: {type(e2).__name__}: {e2}')
                    time.sleep(poll)
                    continue
                continue
            if typ != 'OK' or not data or not data[0]:
                log('gmail', f'IMAP search returned typ={typ}')
                time.sleep(poll)
                continue
            ids = data[0].split()
            recent_ids = ids[-20:] if len(ids) > 20 else ids
            enforce_age = True  # always enforce recency; _is_recent_msg tolerates clock skew
            for i in reversed(recent_ids):
                try:
                    i_str = i.decode() if isinstance(i, bytes) else str(i)
                    typ, mdata = M.fetch(i_str, '(BODY.PEEK[])')
                    raw_msg = _get_fetch_raw_bytes(mdata)
                    if not raw_msg:
                        continue
                    msg = email_lib.message_from_bytes(raw_msg)
                except Exception:
                    continue
                msg_id = msg.get('Message-ID', '').strip()
                if msg_id and (msg_id in SEEN_GMAIL_MSG_IDS or msg_id in ignored):
                    continue
                if enforce_age and not _is_recent_msg(msg):
                    continue
                otp = _otp_from_msg(msg)
                if otp:
                    if msg_id:
                        SEEN_GMAIL_MSG_IDS.add(msg_id)
                    try:
                        M.store(i_str, '+FLAGS', '\\Seen')
                    except Exception:
                        pass
                    log('gmail', f'Gmail OTP received: {otp}')
                    return otp
            time.sleep(poll)
        fail('gmail', f'OTP did not arrive in Gmail {gmail} within {timeout}s')
    finally:
        try:
            M.logout()
        except Exception:
            pass

def read_invite_codes(path=None):
    init_data_dirs()
    p = path or INVITE_FILE_DEFAULT
    if not os.path.exists(p):
        return []
    seen = set()
    out = []
    # utf-8-sig automatically strips UTF-8 BOM if saved by Notepad
    with open(p, encoding='utf-8-sig') as f:
        for ln in f:
            c = ln.strip()
            if c and c not in seen:
                seen.add(c)
                out.append(c)
    return out

def remove_invite_code(path, code):
    p = path or INVITE_FILE_DEFAULT
    if not os.path.exists(p):
        return
    keep = [c for c in read_invite_codes(p) if c != code]
    tmp_p = f"{p}.{os.getpid()}.tmp"
    try:
        with open(tmp_p, 'w', encoding='utf-8') as f:
            f.write('\n'.join(keep) + ('\n' if keep else ''))
        last_err = None
        for attempt in range(5):
            try:
                os.replace(tmp_p, p)  # Atomic on both Windows and POSIX
                last_err = None
                break
            except OSError as e:
                last_err = e
                time.sleep(0.4 * (attempt + 1))
        if last_err is not None:
            raise last_err
    finally:
        try:
            if os.path.exists(tmp_p):
                os.remove(tmp_p)
        except OSError:
            pass
    log('invite', f'code {code} removed from {p} ({len(keep)} remaining)')

AC_GRAPHQL = 'https://accountscenter.meta.com/api/graphql/'
AC_PAGE = 'https://accountscenter.meta.com/account_overview/contact_points'
AC_ASBD_ID = '359341'
# Doc-IDs captured from real Accounts Center traffic (stable until Meta redeploys).
AC_DOC_ADD_CONTACT = '8929017267200363'        # FXAccountsCenterUnifiedAddContactPointMutation
AC_DOC_CONFIRM_CONTACT = '33295109726800304'   # FXAccountsCenterUnifiedVerifyContactPointMutation
AC_DOC_REMOVE_CONTACT = '9412959315500210'     # FXAccountsCenterUnifiedDeleteContactPointMutation

def _ac_b64_email(email):
    return base64.b64encode((email or '').encode('utf-8')).decode('ascii')

def _ac_jazoest(fb_dtsg):
    return '2' + str(sum(ord(c) for c in (fb_dtsg or '')))

def _ac_extract_tokens(html):
    if not html or not isinstance(html, str):
        return None, None
    import html as html_lib
    unescaped = html_lib.unescape(html)
    fb_dtsg = None
    lsd = None

    # 1. DTSG token: array format ["DTSGInitData", [...], {"token": "..."}]
    m = re.search(r'\["DTSGInit(?:ial)?Data",\s*\[.*?\]\s*,\s*\{[^}]*?"token"\s*:\s*"([^"]+)"', unescaped)
    if m:
        fb_dtsg = m.group(1)
    elif (m := re.search(r'\{"DTSGInit(?:ial)?Data"[^}]*?"token"\s*:\s*"([^"]+)"', unescaped)):
        fb_dtsg = m.group(1)
    elif (m := re.search(r'<input[^>]+(?:name=["\']fb_dtsg["\'][^>]+value=["\']([^"\']+)["\']|value=["\']([^"\']+)["\'][^>]+name=["\']fb_dtsg["\'])', unescaped)):
        fb_dtsg = m.group(1) or m.group(2)
    elif (m := re.search(r'"fb_dtsg"\s*:\s*"([^"]+)"', unescaped)):
        fb_dtsg = m.group(1)

    # 2. Meta LSD token: ["LSD", [...], {"token": "..."}]
    m2 = re.search(r'\["LSD",\s*\[.*?\]\s*,\s*\{[^}]*?"token"\s*:\s*"([^"]+)"', unescaped)
    if m2:
        lsd = m2.group(1)
    elif (m2 := re.search(r'\{"LSD"[^}]*?"token"\s*:\s*"([^"]+)"', unescaped)):
        lsd = m2.group(1)
    elif (m2 := re.search(r'<input[^>]+(?:name=["\']lsd["\'][^>]+value=["\']([^"\']+)["\']|value=["\']([^"\']+)["\'][^>]+name=["\']lsd["\'])', unescaped)):
        lsd = m2.group(1) or m2.group(2)
    elif (m2 := re.search(r'"lsd"\s*:\s*"([^"]+)"', unescaped)):
        lsd = m2.group(1)

    return fb_dtsg, lsd

def _dig(obj, keys):
    cur = obj
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur

def _ac_has_explicit_failure(obj):
    """Recursively detect an explicit failure flag anywhere in a GraphQL payload."""
    if isinstance(obj, dict):
        for key in ('success', 'successful'):
            if obj.get(key) is False:
                return True
        return any(_ac_has_explicit_failure(v) for v in obj.values())
    if isinstance(obj, (list, tuple)):
        return any(_ac_has_explicit_failure(v) for v in obj)
    return False

def _is_ac_mutation_success(data):
    if not isinstance(data, dict):
        return False
    if data.get('error'):
        return False
    payload = data.get('data')
    if payload is None:
        return False
    if not isinstance(payload, dict):
        # Scalars/lists are not valid GraphQL mutation success shapes
        return False
    if not payload:
        return False
    if all(v is None for v in payload.values()):
        return False
    if _ac_has_explicit_failure(payload):
        return False
    return True

def _ac_cp_id_from(data):
    if not isinstance(data, dict):
        return None
    if data.get('errors') or data.get('error'):
        return None
    for path in (
        ('data', 'add_contact_point', 'contact_point', 'id'),
        ('data', 'addContactPoint', 'contactPoint', 'id'),
        ('data', 'contact_point', 'id'),
        ('data', 'node', 'id'),
    ):
        v = _dig(data, path)
        if v:
            return str(v)
    return None

def _ac_graphql(api, friendly, doc_id, variables, referer=None):
    if not doc_id:
        return None
    # Keep muse.ai registration headers from leaking into Accounts Center calls.
    _saved_hatch = {}
    try:
        for _hk in ('x-hatch-caa-reg-entry-point', 'x-hatch-waterfall-id'):
            if _hk in api.headers:
                _saved_hatch[_hk] = api.headers.pop(_hk)
    except Exception:
        _saved_hatch = {}
    try:
        cached = getattr(api, '_ac_tokens', None)
        if cached and cached[0]:
            fb_dtsg, lsd = cached
        else:
            nav_headers = {
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Origin': 'https://accountscenter.meta.com',
                'Referer': 'https://accountscenter.meta.com/',
                'Sec-Fetch-Site': 'same-origin',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Dest': 'document',
            }
            r = api.get(AC_PAGE, headers=nav_headers, timeout=30)
            fb_dtsg, lsd = _ac_extract_tokens(r.text)
            if not fb_dtsg:
                log('ac', f'{friendly}: fb_dtsg token not found on Accounts Center page (session not linked)')
                return None
            api._ac_tokens = (fb_dtsg, lsd)
        # Use real account id (c_user cookie) for av when available; fallback to 0
        av_val = '0'
        try:
            c_user = api.cookies.get('c_user')
            if c_user:
                av_val = str(c_user)
        except Exception:
            pass
        body = {
            '__a': '1',
            '__req': 'a',
            '__comet_req': '8',
            'dpr': '1',
            '__user': av_val,
            'av': av_val,
            'fb_api_caller_class': 'RelayModern',
            'fb_api_req_friendly_name': friendly,
            'server_timestamps': 'true',
            'variables': js_json(variables),
            'doc_id': doc_id,
            'fb_dtsg': fb_dtsg,
            'jazoest': _ac_jazoest(fb_dtsg),
            'lsd': lsd or '',
        }
        headers = {
            'Accept': '*/*',
            'Content-Type': 'application/x-www-form-urlencoded',
            'Origin': 'https://accountscenter.meta.com',
            'Referer': referer or AC_PAGE,
            'Sec-Fetch-Site': 'same-origin',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Dest': 'empty',
            'x-asbd-id': AC_ASBD_ID,
            'x-fb-friendly-name': friendly,
            'x-fb-lsd': lsd or '',
        }
        r = api.post(AC_GRAPHQL, data=body, headers=headers, timeout=30)
        if r.status_code != 200:
            log('ac', f'{friendly} -> HTTP {r.status_code}: {r.text[:200]}')
            try:
                setattr(api, '_ac_tokens', None)
            except Exception:
                pass
            return None
        data = r.json()
        if not _is_ac_mutation_success(data):
            log('ac', f'{friendly} -> {str(data)[:300]}')
            if 'dtsg' in str(data).lower() or 'checkpoint' in str(data).lower():
                setattr(api, '_ac_tokens', None)
            return None
        return data
    except Exception as e:
        log('ac', f'{friendly} failed: {type(e).__name__}: {e}')
        return None
    finally:
        try:
            api.headers.update(_saved_hatch)
        except Exception:
            pass

def _ac_manual_pause(title, steps):
    was_active = bool(TUI.get('active'))
    if was_active:
        try:
            TUI['active'] = False
            if _tui_tty():
                sys.stdout.write('\033[?25h')
                sys.stdout.flush()
        except Exception:
            pass
    try:
        print(col(f'\n  {title}', 'b'))
        for s in steps:
            print(col(f'   - {s}', 'w'))
        print(col('   > Press Enter once completed in browser to continue (or type "s" to skip).', 'y'))
        if not sys.stdin.isatty():
            log('ac', 'non-interactive environment - cannot wait for manual assist')
            return False
        ans = input().strip().lower()
        return ans != 's'
    except (EOFError, KeyboardInterrupt, UnicodeError):
        log('ac', 'manual assist skipped/interrupted by user')
        return False
    finally:
        if was_active:
            try:
                TUI['active'] = True
                if _tui_tty():
                    sys.stdout.write('\033[?25l')
                    sys.stdout.flush()
                tui_flush()
            except Exception:
                pass

def _ac_has_contact(api, email):
    """Best-effort check whether `email` is listed on the Accounts Center contact page.

    Returns True/False when the page can be read, and None when it cannot be
    determined (HTTP != 200, network error, or an unlinked/login page).
    """
    target = (email or '').strip().lower()
    if not target:
        return None
    # Keep muse.ai registration headers from leaking into Accounts Center.
    _saved = {}
    try:
        for _hk in ('x-hatch-caa-reg-entry-point', 'x-hatch-waterfall-id'):
            if _hk in api.headers:
                _saved[_hk] = api.headers.pop(_hk)
    except Exception:
        _saved = {}
    try:
        nav_headers = {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Origin': 'https://accountscenter.meta.com',
            'Referer': 'https://accountscenter.meta.com/',
            'Sec-Fetch-Site': 'same-origin',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Dest': 'document',
        }
        r = api.get(AC_PAGE, headers=nav_headers, timeout=30)
        if r.status_code != 200:
            log('ac', f'contact check: HTTP {r.status_code}')
            return None
        text = (r.text or '').lower()
        # A real contact list must render at least one email address. Without any
        # '@' we are almost certainly on a login/landing page: treat as unknown.
        if '@' not in text:
            return None
        markers = ('contact_points', 'contactpoint', 'contact point', 'contact info', 'contact_info')
        if not any(m in text for m in markers):
            # Looks like a login / error page rather than the contact list.
            return None
        return target in text
    except Exception as e:
        log('ac', f'contact check failed: {type(e).__name__}: {e}')
        return None
    finally:
        try:
            api.headers.update(_saved)
        except Exception:
            pass

def ac_add_email(api, temp_email):
    v = {
        'country': 'US',
        'contact_point': temp_email,
        'contact_point_type': 'EMAIL',
        'family_device_id': 'device_id_fetch_datr',
        'client_mutation_id': f'mutation_id_{int(time.time() * 1000)}',
    }
    data = _ac_graphql(api, 'FXAccountsCenterUnifiedAddContactPointMutation', AC_DOC_ADD_CONTACT, v)
    if data is None:
        # The mutation may have landed even though the response failed or lagged.
        # Re-check the page before manual assist so the email is never added twice.
        for _ in range(2):
            if _ac_has_contact(api, temp_email) is True:
                log('ac', f'{temp_email} is already listed on the account - continuing without adding again')
                return temp_email
            time.sleep(1)
        if _ac_manual_pause('STEP 1 — ADD EMAIL TO ACCOUNT (Manual Assist)', [
            'Log in to https://accountscenter.meta.com with the account just created.',
            f'Personal details -> Contact info -> Add email -> enter: {temp_email}',
            'Confirmation code will be fetched automatically from Fusion in Step 2 — just paste it.',
        ]):
            log('ac', 'step 1 marked completed by user')
            return temp_email
        return None
    cp_id = _ac_cp_id_from(data)
    log('ac', f'add email request accepted (auto){" - contact point id " + cp_id if cp_id else ""}')
    return temp_email

def _ac_attest(prompt):
    """Require an explicit 'yes' from the user before a destructive step."""
    was_active = bool(TUI.get('active'))
    if was_active:
        try:
            TUI['active'] = False
            if _tui_tty():
                sys.stdout.write('\033[?25h')
                sys.stdout.flush()
        except Exception:
            pass
    try:
        if not sys.stdin.isatty():
            return False
        ans = input(prompt).strip().lower()
        return ans in ('yes', 'y')
    except (EOFError, KeyboardInterrupt, UnicodeError):
        return False
    finally:
        if was_active:
            try:
                TUI['active'] = True
                if _tui_tty():
                    sys.stdout.write('\033[?25l')
                    sys.stdout.flush()
                tui_flush()
            except Exception:
                pass

def ac_confirm_email(api, temp_email, otp):
    v = {
        'contact_point': temp_email,
        'contact_point_type': 'EMAIL',
        'pin_code': otp,
        'family_device_id': 'device_id_fetch_datr',
        'client_mutation_id': f'mutation_id_{int(time.time() * 1000)}',
        'contact_point_source': 'fx_settings',
    }
    data = _ac_graphql(api, 'FXAccountsCenterUnifiedVerifyContactPointMutation', AC_DOC_CONFIRM_CONTACT, v)
    if data is None:
        if not _ac_manual_pause('STEP 2 — ENTER CONFIRMATION CODE (Manual Assist)', [
            f'Paste this code into the confirmation box for {temp_email} in browser:',
            f'   OTP CODE: {otp}',
        ]):
            return False
        # Retry real verification now that the user has entered the code.
        data2 = _ac_graphql(api, 'FXAccountsCenterUnifiedVerifyContactPointMutation', AC_DOC_CONFIRM_CONTACT, v)
        if data2 is not None:
            log('ac', 'step 2 verified via API retry (auto)')
            return True
        # Cannot verify programmatically -> require explicit attestation.
        log('ac', 'step 2 cannot be verified automatically - explicit confirmation required')
        if not _ac_attest(f'   Type "yes" only if {temp_email} shows as "Confirmed" on the account: '):
            log('ac', 'step 2 not attested - aborting before deleting default email')
            return False
        log('ac', 'step 2 attested by user')
        return True
    log('ac', 'email verified via OTP (auto)')
    return True

def ac_remove_email(api, default_email):
    v = {
        'contact_point_id': _ac_b64_email(default_email),
        'contact_point_type': 'EMAIL',
        'family_device_id': 'device_id_fetch_datr',
        'client_mutation_id': f'mutation_id_{int(time.time() * 1000)}',
    }
    data = _ac_graphql(api, 'FXAccountsCenterUnifiedDeleteContactPointMutation', AC_DOC_REMOVE_CONTACT, v)
    if data is None:
        if not _ac_manual_pause('STEP 3 — REMOVE DEFAULT EMAIL (Manual Assist)', [
            f'Personal details -> Contact info -> click Remove next to {default_email}.',
        ]):
            return False
        # If the session can read the page, verify the default email is really gone.
        # Meta can take a moment to propagate the removal, so retry before aborting.
        present = None
        for attempt in range(3):
            present = _ac_has_contact(api, default_email)
            if present is not True:
                break
            if attempt < 2:
                log('ac', f'step 3 not verified yet - {default_email} still listed, re-checking...')
                time.sleep(2)
        if present is True:
            log('ac', f'step 3 not verified - {default_email} still present on the account')
            return False
        if present is None:
            log('ac', 'step 3 cannot be verified automatically - explicit confirmation required')
            if not _ac_attest(f'   Type "yes" only if {default_email} is no longer listed on the account: '):
                log('ac', 'step 3 not attested - aborting before finishing swap')
                return False
        log('ac', 'step 3 marked completed by user')
        return True
    log('ac', 'default email removed via API (auto)')
    return True

def swap_default_email(api, default_email):
    TUI['step'] = 'swap: adding temp email'
    log('swap', 'Step 1: Adding Fusion temp email as contact point...')
    temp = fusion_new_email()
    if ac_add_email(api, temp) is None:
        return (False, None)
    _record_pending_swap(temp, default_email, 'added')
    TUI['step'] = 'swap: waiting temp OTP'
    log('swap', f'Step 2: Waiting for OTP sent to {temp}...')
    otp = fusion_wait_otp(temp, timeout=60)
    TUI['step'] = 'swap: confirming temp email'
    if not ac_confirm_email(api, temp, otp):
        return (False, None)
    log('swap', f'Temp email {temp} successfully configured on account')
    _record_pending_swap(temp, default_email, 'confirmed')
    time.sleep(2)  # Allow Meta backend database replication to propagate new contact point
    TUI['step'] = 'swap: removing default email'
    log('swap', f'Step 3: Removing default email {default_email} from account...')
    if not ac_remove_email(api, default_email):
        log('swap', f'Swap recorded in {PENDING_SWAP_FILE} - temp email {temp} can be recovered manually')
        return (False, None)
    _record_pending_swap(temp, default_email, 'completed')
    log('swap', f'Default email detached - {temp} is now the active account email')
    return (True, temp)

def card_brand(num):
    if num.startswith('4'):
        return 'VISA'
    if re.match('^(5[1-5]|2(22[1-9]|2[3-9][0-9]|3[0-9]{2}|7([01][0-9]|20)))', num):
        return 'MASTERCARD'
    if num.startswith(('34', '37')):
        return 'AMEX'
    if num.startswith('6011') or num.startswith('65'):
        return 'DISCOVER'
    return 'UNKNOWN'

def accept_tos(api):
    body = {'saw_meta_ai_tos': False, 'client_request_timestamp': str(int(time.time())), 'entry_point': 'web_onboarding_flow', 'nux_type': 'ROW', 'disclosure_version': 'HATCH_ROW_v0', 'disclosure_text': "By continuing, you agree to the Muse Terms and Meta's AI Terms, which include important information about your rights and responsibilities. Learn more about Muse's approach to privacy in the Muse Privacy Policy."}
    r = app_request(api, 'POST', '/api/hatch/accept-tos', body, referer=MUSE_BASE + '/access/disclosure', fatal=False)
    if r is None:
        log('tos', 'accept-tos 403: skipped (account gated / already accepted)')
    else:
        log('tos', f'accept-tos: {r.json()}')

def redeem_code(api, ref_code):
    r = app_post(api, '/api/hatch/invite-code/redeem', {'code': ref_code, 'supportsRedemptionStatus': True})
    data = r.json()
    log('redeem', f"success={data.get('success')} redemption={data.get('redemptionStatus')} waitlist={data.get('waitlistStatus')}")
    return data

def checkout_init(api, product_id, receiver_id):
    session_id = f'upl_{int(time.time() * 1000)}_{uuid.uuid4()}'
    body = {'amount': '1.00', 'currency': 'USD', 'sessionId': session_id, 'productId': product_id, 'receiverId': receiver_id, 'productData': []}
    r = app_post(api, '/api/checkout/init', body, referer=MUSE_BASE + '/access/verification')
    data = r.json()
    log('vcc-init', f"orderId={data.get('orderId')} receiver={data.get('receiverName')}")
    return (session_id, data)

def get_encryption_key(api, upl_session_id, product_id):
    body = {'deviceId': 'device_id', 'paymentType': 'ECP', 'uplSessionId': upl_session_id, 'productId': product_id, 'otcSessionId': str(uuid.uuid4()), 'otcType': 'card'}
    r = app_post(api, '/api/ptt/encryption-key', body, referer=MUSE_BASE + '/access/verification')
    data = r.json()
    if not data.get('trustChain'):
        fail('vcc-key', f'trustChain is empty: {r.text[:200]}')
    log('vcc-key', f"key id={data.get('id')} devExternal={data.get('devExternal')}")
    return (body['otcSessionId'], data)
DECLINE_MARKERS = ('declined', 'do not honor', 'insufficient funds', '2078019', 'card declined')

def _decline_hit(text):
    t = (text or '').lower()
    return any((m in t for m in DECLINE_MARKERS))

def credential_create(api, upl_session_id, otc_session_id, init_data, product_id, receiver_id, ptt_token, vcc, num, exp):
    num = re.sub('\\D', '', num)
    body = {'operation': 'CREATE', 'uplSessionId': upl_session_id, 'ecpCheckoutSessionId': None, 'orderId': init_data['orderId'], 'amount': '1.00', 'currencyCode': 'USD', 'paymentContainerMode': init_data.get('paymentContainerMode', 'LIVE'), 'productId': product_id, 'receiverId': receiver_id, 'platformTrustToken': ptt_token, 'cardFirst6': num[:6], 'cardLast4': num[-4:], 'expiryMonth': exp[:2], 'expiryYear': '20' + exp[2:4], 'zip': vcc.get('zip', ''), 'countryCode': vcc.get('country', 'US'), 'otcSessionId': otc_session_id, 'otcType': 'card'}
    r = app_post(api, '/api/checkout/credential', body, referer=MUSE_BASE + '/access/verification')
    data = r.json()
    mut = data.get('mutation') or {}
    if not mut.get('success'):
        err = mut.get('error') or {}
        msg = f"{err.get('title')}: {err.get('description')}"
        if _decline_hit(msg) or _decline_hit(str(err.get('error_code') or '')):
            fail('vcc-cred', f"Card DECLINED by bank ({err.get('title')}). Please use another card — retrying the same card will not work.")
        _guard_transient(data, 'checkout/credential')
        fail('vcc-cred', msg)
    _guard_transient(data, 'checkout/credential')
    cred_id = mut.get('credentialId')
    log('vcc-cred', f'credentialId={cred_id}')
    return cred_id

def checkout_finalize(api, upl_session_id, otc_session_id, init_data, product_id, receiver_id, credential_id):
    container_ptt = ptt_container_token(init_data['orderId'], receiver_id, init_data.get('paymentContainerMode', 'LIVE'), '1.00', 'USD', credential_id)
    body = {'uplSessionId': upl_session_id, 'ecpCheckoutSessionId': None, 'orderId': init_data.get('orderId', ''), 'selectedCredentialId': credential_id, 'credentialType': 'CREDIT_CARD', 'countryCode': CFG['vcc'].get('country', 'US'), 'amount': '1.00', 'currencyCode': 'USD', 'payButtonClickId': str(uuid.uuid4()), 'paymentRequestId': str(uuid.uuid4()), 'platformTrustToken': container_ptt, 'paymentContainerMode': init_data.get('paymentContainerMode', 'LIVE'), 'otcSessionId': otc_session_id, 'otcType': 'card', 'productId': product_id, 'receiverId': receiver_id}
    r = app_post(api, '/api/checkout/finalize', body, referer=MUSE_BASE + '/access/verification')
    data = r.json()
    status = data.get('checkoutStatus')
    log('vcc-final', f"checkoutStatus={status} orderId={data.get('orderId')}")
    if data.get('error'):
        log('vcc-final', f"error: {data['error']}")
        if _decline_hit(json.dumps(data['error'])):
            fail('vcc-final', 'Card DECLINED by bank during finalization. Please use another card.')
    if isinstance(status, str) and _decline_hit(status):
        fail('vcc-final', f'Card DECLINED by bank during finalization (status={status}). Please use another card.')
    if isinstance(status, str) and status.upper() in ('FAILED', 'REJECTED', 'ERROR'):
        fail('vcc-final', f'Checkout failed (status={status})')
    _guard_transient(data, 'checkout/finalize')
    return data
OTP_PROVIDER = None
VCC_PROVIDER = None

def _login_common(api, csrf, email, gmail_password=None):
    pw = gmail_password or CFG.get('gmail_password') or ''
    ignore_ids = set()
    if pw and _is_gmail_address(email):
        ignore_ids = gmail_snapshot_recent_msg_ids(email, pw)
    TUI['step'] = 'sending OTP'
    send_otp(api, csrf, email)
    TUI['step'] = 'waiting for OTP'
    if FUSION_DOMAIN in email.lower():
        otp = fusion_wait_otp(email)
    elif pw and _is_gmail_address(email):
        otp = gmail_wait_otp(email, pw, ignore_msg_ids=ignore_ids)
    elif OTP_PROVIDER is not None:
        otp = OTP_PROVIDER(email)
    else:
        otp = input('OTP: ').strip()
    TUI['step'] = 'confirming OTP'
    data = confirm_otp(api, csrf, otp)
    sess = data.get('auth_meta_session') or {}
    if sess.get('frl_access_token'):
        log('login', f"email already registered -> logging in, frl_account_id={sess.get('frl_account_id')}")
        log('login', 'account already exists on Meta (registered on phone/web) - continuing with login')
        return (sess['frl_access_token'], sess.get('frl_account_id'))
    if data.get('registration_state') == 'completion_required':
        log('register', 'new email -> registering new account')
        first = CFG['first_name']
        last = CFG['last_name']
        dob = CFG['date_of_birth']
        if CFG.get('random_profile'):
            first, last, dob = random_profile()
        cfg_dob = CFG.get('dob')
        if cfg_dob and cfg_dob.lower() != 'random':
            dob = cfg_dob
        log('profile', f'{first} {last}, {dob}')
        TUI['step'] = 'registering profile'
        reg = register_completion(api, csrf, first, last, dob)
        sess = reg.get('auth_meta_session') or {}
        log('register', f"frl_account_id={sess.get('frl_account_id')}")
        return (sess.get('frl_access_token'), sess.get('frl_account_id'))
    fail('auth', f'unknown confirm-otp response: {str(data)[:160]}')
def probe_ccv_status(api):
    try:
        r = api.get(MUSE_BASE + '/api/hatch/ccv-status', headers={'Accept': '*/*', 'Referer': MUSE_BASE + '/access/verification', 'Origin': MUSE_BASE}, timeout=30)
    except Exception as e:
        log('gate', f'ccv-status probe failed: {type(e).__name__}: {e}')
        return (None, None)
    finally:
        pass
    if r.status_code != 200:
        log('gate', f'ccv-status HTTP {r.status_code} ({r.text[:80].strip() or "unreadable"})')
        return (r.status_code, None)
    try:
        val = r.json().get('ccv_status')
    except Exception:
        val = None
    finally:
        pass
    log('gate', f'ccv_status={val!r}')
    return (200, val)

def _vcc_gate_flow(api):
    vcc = CFG.get('vcc', {})
    if not vcc.get('number') and VCC_PROVIDER is not None:
        vcc = VCC_PROVIDER() or {}
        if vcc.get('number'):
            CFG['vcc'] = vcc
    if not vcc.get('number'):
        fail('gate', 'Account hit verification gate: configure VCC first (menu [4]) or enter when prompted')
    num, exp = check_vcc(vcc)
    final = vcc_steps(api, vcc, num, exp)
    log('gate', f"VCC completed: {final.get('checkoutStatus')}")
    _, ccv = probe_ccv_status(api)
    log('gate', f'post-VCC ccv_status: {ccv!r}')
    inv = app_request(api, 'GET', '/api/hatch/invite', fatal=False)
    if inv is None:
        fail('gate', 'invite still returning 403 after VCC; inspect manually at https://muse.ai/access/verification')
    return inv

def _post_auth(api, ref_code=None):
    save_account(api)
    accept_tos(api)
    code, ccv = probe_ccv_status(api)
    inv = None
    if ccv == 'not_required':
        log('gate', 'registered WITHOUT VCC (ccv_status=not_required)')
        inv = app_request(api, 'GET', '/api/hatch/invite', fatal=False)
    elif ccv == 'blocked':
        fail('gate', 'ccv_status=blocked (age-blocked); resolve manually at https://muse.ai/access/verification')
    elif ccv == 'ccv_required':
        log('gate', 'account requires VCC verification (ccv_status=ccv_required)')
        inv = _vcc_gate_flow(api)
    else:
        inv = app_request(api, 'GET', '/api/hatch/invite', fatal=False)
        if inv is None:
            log('gate', 'invite step bypassed / already completed — proceeding to redeem')
    if inv is not None:
        try:
            log('invite', f"invite_state={inv.json().get('invite_state')}")
        except Exception:
            pass
    if ref_code:
        return redeem_code(api, ref_code)
    return None

MAX_ACCOUNTS_PER_CODE = 60

def flow_auto_farm(st):
    CFG['proxy_enabled'] = bool(st['proxy'])
    CFG['proxy'] = st['proxy']
    PROXIES.clear()
    PROXIES.update(get_proxies(CFG))
    CFG['gmail_password'] = st.get('gmail_app_password') or ''
    CFG['dob'] = st.get('dob') or 'random'
    email = (st.get('default_email') or '').strip() or fail('config', 'default email is not set (configure via menu [2])')
    pw = (st.get('gmail_app_password') or '').strip() or fail('config', 'Gmail app password is not set (configure via menu [2])')
    path = (st.get('invite_file') or '').strip() or INVITE_FILE_DEFAULT
    if not read_invite_codes(path):
        fail('invite', f'file {path} is empty or does not exist')
    TUI['account'] = _tui_mask_email(email)
    TUI['reff'] = mask_secret(st.get('reff') or '') if st.get('reff') else 'N/A'
    TUI['proxy'] = mask_proxy(st.get('proxy') or '') or 'system/VPN (must be US)'
    TUI['dob'] = st.get('dob') or 'random'
    TUI['max_per_code'] = MAX_ACCOUNTS_PER_CODE
    TUI['accounts_saved'] = _count_saved_accounts(fallback=int(TUI.get('accounts_saved') or 0))
    TUI['accounts_this_code'] = 0
    TUI['step'] = 'starting'
    lock_fd = None
    lock_fd = _acquire_farm_lock()
    tui_start('Auto Farm & Recycle (Gmail -> Fusion -> Next Code)')
    try:
        while True:
            codes = read_invite_codes(path)
            TUI['codes_remaining'] = len(codes)
            if not codes:
                log('farm', 'Completed - no more invite codes remaining in file')
                break
            code = codes[0]
            TUI['last_code'] = code
            log('farm', f'Invite code: {code} ({len(codes)} remaining)')
            result = _farm_one_code(email, pw, code)
            if result == 'exhausted':
                remove_invite_code(path, code)
                log('farm', f'Code {code} exhausted/dropped - moving to next code')
                continue
            fail('farm', f'farm cycle stopped on code {code}')
    finally:
        _release_farm_lock(lock_fd)
        tui_stop()
    print(col('Auto farm completed - all invite codes processed.', 'g'))

def _farm_one_code(email, pw, code):
    n = 0
    while True:
        n += 1
        TUI['accounts_this_code'] = n - 1
        if n > MAX_ACCOUNTS_PER_CODE:
            log('farm', f'Code {code} reached limit of {MAX_ACCOUNTS_PER_CODE} accounts - moving to next code')
            return 'exhausted'
        TUI['step'] = f'account #{n} - starting session'
        log('farm', f'--- Account #{n} with default email {email} ---')
        status = _farm_one_acc(email, pw, code)
        if status == 'redeemed':
            TUI['accounts_this_code'] = n
            continue
        return status

def _farm_one_acc(email, pw, code):
    CFG['email'] = email
    TUI['step'] = 'opening session'
    api, csrf = start_flow()
    TUI['step'] = 'login / OTP'
    token, _ = _login_common(api, csrf, email, gmail_password=pw)
    if not token:
        fail('auth', 'missing frl_access_token')
    TUI['step'] = 'bootstrapping session'
    bootstrap_session(api, token)
    TUI['step'] = 'auth gate check'
    _post_auth(api)  # handles TOS + CCV gate + invite check; fail() with clear error on issues
    TUI['step'] = 'redeeming invite code'
    data = redeem_code(api, code)
    success = bool(data.get('success'))
    redemption_status = str(data.get('redemptionStatus') or '').lower()
    log('farm', f'redeem success={success} redemption={data.get("redemptionStatus")} waitlist={data.get("waitlistStatus")}')
    recovered = False
    if not success:
        if redemption_status == 'already_redeemed':
            recovered = True
            log('farm', f'Account already redeemed an invite code - code {code} is still valid, proceeding to swap email to recycle Gmail')
        else:
            log('farm', f'Code {code} no longer accepted ({redemption_status or "exhausted"}) - dropping code from queue')
            # Free Gmail before exiting this code; fail loudly if swap did not confirm
            TUI['step'] = 'swapping email (code exhausted)'
            swap_ok, replacement_email = swap_default_email(api, email)
            if not swap_ok or not replacement_email:
                fail('farm', 'Email swap was not confirmed during code exhaustion - stopping farm to avoid duplicate accounts')
            return 'exhausted'
    TUI['step'] = 'swapping email'
    swap_ok, replacement_email = swap_default_email(api, email)
    if not swap_ok or not replacement_email:
        fail('farm', 'Email swap was not confirmed - stopping farm to avoid duplicate accounts')
    TUI['step'] = 'saving output'
    dob_val = CFG.get('dob') or 'random'
    out_code = '' if recovered else code
    saved = save_account_output(replacement_email, code=out_code, dob=dob_val)
    if not saved:
        saved = save_account_output(replacement_email, code=out_code, dob=dob_val)
    if not saved:
        fail('output', 'failed to save account record after swap - stopping farm to avoid losing accounts')
    log('farm', f'Account saved to data/output/accounts.txt: {replacement_email} - default email ready for next account')
    TUI['step'] = 'idle'
    return 'redeemed'

def normalize_expiry(exp):
    d = re.sub('\\D', '', exp or '')
    if len(d) == 4:
        return d
    if len(d) == 6:
        return d[:2] + d[4:]
    fail('config', f"Expiry '{exp}' is invalid (use MM/YY)")

def check_vcc(vcc):
    for k in ('number', 'expiry', 'cvv'):
        if not vcc.get(k):
            fail('config', f'vcc.{k} is empty')
    c = (vcc.get('country') or 'US').upper().strip()
    c = COUNTRY_ALIAS.get(c, c)
    if c not in SUPPORTED_COUNTRIES:
        fail('config', f"Country '{vcc.get('country')}' is not supported; choose from: {', '.join(SUPPORTED_COUNTRIES)}")
    vcc['country'] = c
    num = re.sub('\\D', '', vcc['number'])
    if len(num) < 13:
        fail('config', 'Card number is invalid')
    return (num, normalize_expiry(vcc['expiry']))

class TransientCheckout(Exception):
    pass
TRANSIENT_MARKERS = ('request not completed', 'temporary problem', 'try again later', 'please try again')

def _transient_hit(text):
    t = (text or '').lower()
    return any((m in t for m in TRANSIENT_MARKERS))

def _guard_transient(payload, where):
    s = payload if isinstance(payload, str) else json.dumps(payload or {})
    if _transient_hit(s):
        raise TransientCheckout(f'{where}: server requested retry later')

def _vcc_steps_once(api, vcc, num, exp):
    product_id = CFG.get('product_id')
    receiver_id = CFG.get('receiver_id')
    upl_session_id, init_data = checkout_init(api, product_id, receiver_id)
    _guard_transient(init_data, 'checkout/init')
    sup = (init_data.get('paymentMethod') or {}).get('supportedCardTypes') or []
    brand = card_brand(num)
    log('vcc-brand', f"Card={brand} supported={sup or '?'}")
    if sup and brand not in sup:
        fail('vcc', f"Card {brand} is not supported by {sup}; use {'/'.join(sup)}")
    live_countries = [c.get('countryCode') for c in ((init_data.get('paymentMethod') or {}).get('countryPostalPolicy') or {}).get('countries', [])]
    country = vcc.get('country', 'US')
    if live_countries and country not in live_countries:
        fail('vcc', f"Country {country} is not supported for live checkout; choose: {', '.join(live_countries)}")
    if not vcc.get('zip'):
        vcc['zip'] = gen_postal(country)
        if vcc['zip']:
            log('addr', f"ZIP/postal auto-generated: {vcc['zip']}")
        else:
            log('vcc', f'warning: no postal code configured for country {country}')
    otc_session_id, key_data = get_encryption_key(api, upl_session_id, product_id)
    ptt = ptt_card_token(key_data['trustChain'][0], num, vcc['cvv'], exp[:2], '20' + exp[2:4])
    log('vcc-ptt', f'E2EE token generated ({len(ptt)} chars)')
    cred_id = credential_create(api, upl_session_id, otc_session_id, init_data, product_id, receiver_id, ptt, vcc, num, exp)
    final = checkout_finalize(api, upl_session_id, otc_session_id, init_data, product_id, receiver_id, cred_id)
    _guard_transient(final, 'checkout/finalize')
    return final

def vcc_steps(api, vcc, num, exp):
    last = None
    for a in range(3):
        try:
            return _vcc_steps_once(api, vcc, num, exp)
        except TransientCheckout as e:
            last = e
        except SystemExit:
            # fail() means a hard failure — do not retry
            raise
        finally:
            pass
        wait = 60 * (a + 1)
        if a < 2:
            log('vcc-retry', f'Transient server error ({last}); waiting {wait}s, retry ({a + 1}/3)')
            time.sleep(wait)
    fail('vcc', f'Failed 3x due to transient server error: {last}')

def flow_vcc_verify():
    email = CFG.get('email') or fail('config', 'email is empty')
    api, csrf = start_flow()
    token, _ = _login_common(api, csrf, email)
    if not token:
        fail('auth', 'missing frl_access_token')
    bootstrap_session(api, token)
    save_account(api)
    _, ccv = probe_ccv_status(api)
    if ccv == 'not_required':
        log('DONE', f'Account {email} WITHOUT VCC (ccv_status=not_required); nothing to verify')
        return
    if ccv == 'blocked':
        fail('gate', 'ccv_status=blocked (age-blocked); resolve manually at https://muse.ai/access/verification')
    log('gate', f'ccv_status={ccv!r}: proceeding with VCC verification')
    inv = _vcc_gate_flow(api)
    try:
        istate = inv.json().get('invite_state')
    except Exception:
        istate = '?'
    finally:
        pass
    log('DONE', f'VCC verify {email}: invite_state={istate}')

def parse_vcc_pipe(s):
    parts = [p.strip() for p in s.split('|')]
    if len(parts) == 4:
        num, mm, yy, cvv = parts
        exp = normalize_expiry(mm + yy)
    elif len(parts) == 3:
        num, exp_raw, cvv = parts
        exp = normalize_expiry(exp_raw)
    else:
        fail('input', 'VCC format: number|MM|YY|cvv  or  number|MMYY|cvv')
    return (re.sub('\\D', '', num), exp, cvv.strip())

def gen_postal(country):
    if country == 'US':
        return f'{random.randint(501, 99950):05d}'
    if country == 'CA':
        a = random.choice('ABCEGHJKLMNPRSTVXY')
        rest = 'ABCEGHJKLMNPRSTVWXZ'
        d = lambda: str(random.randint(0, 9))
        return a + d() + random.choice(rest) + d() + random.choice(rest) + d()
    return ''
"geo.py — fuzzy country matching & US-only proxy validation.\n\n- match_country(text): user types 'indo' / 'Indonesia' / 'id' / 'ID' -> 'ID'.\n  Applies to ALL supported countries (62 codes) with common Indonesian\n  and English aliases + ISO alpha-2/alpha-3 codes.\n- proxy info & US-mandatory: geo_check_proxy(proxy) uses ip-api.com.\n"
COUNTRIES = {'US': ('United States', ['usa', 'amerika', 'amerika serikat', 'america', 'united states of america', 'us', 'u.s.']), 'CA': ('Canada', ['kanada']), 'GB': ('United Kingdom', ['uk', 'inggris', 'england', 'british', 'britania raya']), 'DZ': ('Algeria', ['aljazair']), 'AR': ('Argentina', ['argentina']), 'AU': ('Australia', ['australian', 'aussie', 'aus', 'oz']), 'AT': ('Austria', []), 'BE': ('Belgium', ['belgia']), 'BR': ('Brazil', ['brasil', 'brazil']), 'CL': ('Chile', ['cile']), 'CO': ('Colombia', ['kolombia']), 'CI': ('Ivory Coast', ['cote divoire', 'pantai gading', 'ivory']), 'HR': ('Croatia', ['kroasia']), 'CZ': ('Czechia', ['ceko', 'czech republic', 'czech']), 'DK': ('Denmark', ['denmark']), 'EG': ('Egypt', ['mesir']), 'FI': ('Finland', ['finlandia']), 'FR': ('France', ['prancis', 'france']), 'DE': ('Germany', ['jerman', 'germany']), 'GH': ('Ghana', []), 'HK': ('Hong Kong', ['hongkong', 'hong kong']), 'IS': ('Iceland', ['islandia']), 'IN': ('India', []), 'ID': ('Indonesia', ['indo', 'ri', 'nkri', 'idn', 'republik indonesia', 'indonesian']), 'IE': ('Ireland', ['irlandia']), 'IL': ('Israel', []), 'IT': ('Italy', ['italia', 'italy']), 'JP': ('Japan', ['jepang', 'japan']), 'KE': ('Kenya', []), 'LB': ('Lebanon', ['lebanon']), 'MY': ('Malaysia', ['malaisia', 'malaysia']), 'MX': ('Mexico', ['meksiko', 'mexico']), 'MA': ('Morocco', ['maroko']), 'NL': ('Netherlands', ['belanda', 'holland', 'netherland']), 'NZ': ('New Zealand', ['selandia baru', 'new zealand']), 'NG': ('Nigeria', []), 'NO': ('Norway', ['norwegia']), 'PK': ('Pakistan', []), 'PH': ('Philippines', ['filipina', 'philippine', 'pinoy', 'ph']), 'PL': ('Poland', ['polandia', 'polska']), 'PT': ('Portugal', []), 'QA': ('Qatar', []), 'RO': ('Romania', ['rumania']), 'SA': ('Saudi Arabia', ['arab saudi', 'saudi', 'ksa', 'saudi arabia']), 'SN': ('Senegal', []), 'SG': ('Singapore', ['singapura', 'sgp', 'singapore']), 'ZA': ('South Africa', ['afrika selatan', 'afsel', 'south africa']), 'KR': ('South Korea', ['korea', 'south korea', 'korsel', 'korea selatan', 'kor']), 'ES': ('Spain', ['spanyol', 'espana', 'esy']), 'SE': ('Sweden', ['swedia']), 'CH': ('Switzerland', ['swiss']), 'TW': ('Taiwan', []), 'TH': ('Thailand', ['thai', 'thailand']), 'TR': ('Turkey', ['turki', 'turkiye', 'turkey']), 'AE': ('United Arab Emirates', ['uae', 'emirates', 'uni emirat arab', 'uea', 'dubai']), 'VN': ('Vietnam', ['vietnam', 'vietnamese', 'vn']), 'ZM': ('Zambia', []), 'ZW': ('Zimbabwe', [])}
_ALPHA3 = {'USA': 'US', 'CAN': 'CA', 'GBR': 'GB', 'DZA': 'DZ', 'ARG': 'AR', 'AUS': 'AU', 'AUT': 'AT', 'BEL': 'BE', 'BRA': 'BR', 'CHL': 'CL', 'COL': 'CO', 'CIV': 'CI', 'HRV': 'HR', 'CZE': 'CZ', 'DNK': 'DK', 'EGY': 'EG', 'FIN': 'FI', 'FRA': 'FR', 'DEU': 'DE', 'GHA': 'GH', 'HKG': 'HK', 'ISL': 'IS', 'IND': 'IN', 'IDN': 'ID', 'IRL': 'IE', 'ISR': 'IL', 'ITA': 'IT', 'JPN': 'JP', 'KEN': 'KE', 'LBN': 'LB', 'MYS': 'MY', 'MEX': 'MX', 'MAR': 'MA', 'NLD': 'NL', 'NZL': 'NZ', 'NGA': 'NG', 'NOR': 'NO', 'PAK': 'PK', 'PHL': 'PH', 'POL': 'PL', 'PRT': 'PT', 'QAT': 'QA', 'ROU': 'RO', 'SAU': 'SA', 'SEN': 'SN', 'SGP': 'SG', 'ZAF': 'ZA', 'KOR': 'KR', 'ESP': 'ES', 'SWE': 'SE', 'CHE': 'CH', 'TWN': 'TW', 'THA': 'TH', 'TUR': 'TR', 'ARE': 'AE', 'VNM': 'VN', 'ZMB': 'ZM', 'ZWE': 'ZW'}
_INDEX = {}

def _norm(s):
    return re.sub('[^a-z0-9 ]+', ' ', str(s or '').lower()).replace('  ', ' ').strip()

def _build_index():
    for code, (name, aliases) in COUNTRIES.items():
        terms = {code.lower(), name.lower(), _norm(name)}
        for a in aliases:
            terms.add(a.lower())
            terms.add(_norm(a))
        for t in terms:
            if t:
                _INDEX.setdefault(t, code)
    for a3, code in _ALPHA3.items():
        _INDEX.setdefault(a3.lower(), code)
_build_index()
OFFICIAL = {c: n for c, (n, _) in COUNTRIES.items()}

def match_country(text):
    t = _norm(text)
    if not t:
        return None
    if len(t) == 2 and t.upper() in COUNTRIES:
        return t.upper()
    if t in _INDEX:
        return _INDEX[t]
    return None

def zip_random(country):
    if country == 'US':
        return f'{random.randint(501, 99950):05d}'
    if country == 'CA':
        a = random.choice('ABCEGHJKLMNPRSTVXY')
        rest = 'ABCEGHJKLMNPRSTVWXZ'
        d = lambda: str(random.randint(0, 9))
        return a + d() + random.choice(rest) + d() + random.choice(rest) + d()
    return ''
PROXY_RE = re.compile('^(https?|socks5h?|socks4)://', re.I)

def parse_proxy(text):
    import urllib.parse
    t = (text or '').strip().strip('<>')
    if not PROXY_RE.match(t):
        raise ValueError('Invalid format — example: http://user:pass@host:port or socks5://user:pass@host:port')
    u = urllib.parse.urlparse(t)
    if not u.hostname or not u.port:
        raise ValueError('Host or port is incomplete.')
    return t

def mask_proxy(p):
    import urllib.parse
    if not p:
        return ''
    try:
        u = urllib.parse.urlparse(p)
        if not u.scheme or not u.hostname:
            return ''
        auth = '***:***@' if u.username else ''
        return f'{u.scheme}://{auth}{u.hostname}:{u.port}'
    except Exception:
        return '***'
    finally:
        pass

def geo_check_proxy(proxy, timeout=18):
    try:
        import curl_cffi.requests as cr
    except ImportError:
        return {'ok': False, 'error': 'curl_cffi is not installed.'}
    finally:
        pass
    err = '?'
    for _try in range(3):
        try:
            s = cr.Session(impersonate='chrome', proxies={'http': proxy, 'https': proxy})
            r = s.get('http://ip-api.com/json/?fields=status,country,countryCode,query', timeout=timeout)
            d = r.json()
            if d.get('status') != 'success':
                err = f"ip-api rejected request (status={d.get('status')})."
                continue
            return {'ok': True, 'country_code': d.get('countryCode', ''), 'country': d.get('country', ''), 'ip': d.get('query', '')}
        except Exception as e:
            err = f'{type(e).__name__}: {str(e)[:140]}'
            try:
                s.close()
            except Exception:
                pass
        finally:
            pass
    return {'ok': False, 'error': f'After 3 attempts: {err}'}

def us_verdict(info):
    if not info.get('ok'):
        return (False, f"Proxy cannot be used: {info.get('error', '?')[:160]}")
    if info.get('country_code') != 'US':
        return (False, f"This proxy is located in {info.get('country') or '?'} ({info.get('country_code') or '?'}) — but muse.ai strictly requires a US IP.")
    return (True, info.get('ip', ''))
C = {'g': '\x1b[92m', 'y': '\x1b[93m', 'r': '\x1b[91m', 'c': '\x1b[96m', 'b': '\x1b[94m', 'w': '\x1b[97m', 'bold': '\x1b[1m', '0': '\x1b[0m'}
if os.name == 'nt':
    try:
        os.system('')
    except Exception:
        pass
    finally:
        pass
STATE_FILE = os.path.join(BASE_DIR, '.muse_state.json')
VERSION = '2.5.0'

def col(txt, k):
    if not sys.stdout.isatty():
        return txt
    return f"{C[k]}{txt}{C['0']}"

# === Minimalist ANSI TUI (stdlib only, no box characters) ===
ANSI = {
    'reset': '\033[0m',
    'value': '\033[1;97m',
    'label': '\033[1;96m',
    'ok': '\033[1;92m',
    'warn': '\033[1;93m',
    'err': '\033[1;91m',
    'ts': '\033[90m',
    'dim': '\033[2m',
}
TUI = {
    'active': False,
    'started_at': 0.0,
    'status': 'STOPPED',
    'mode': 'N/A',
    'account': 'N/A',
    'reff': 'N/A',
    'proxy': 'N/A',
    'dob': 'N/A',
    'codes_remaining': 0,
    'accounts_this_code': 0,
    'max_per_code': 0,
    'accounts_saved': 0,
    'last_code': 'N/A',
    'step': 'idle',
}
LOG_BUF = deque(maxlen=5)
TUI_LABEL_W = 19

def _tui_tty():
    try:
        return bool(sys.stdout.isatty())
    except Exception:
        return False

def _tui_color_on():
    try:
        return _tui_tty() and not os.environ.get('NO_COLOR')
    except Exception:
        return False

def _tui_paint(text, key):
    try:
        s = str(text)
    except Exception:
        s = 'N/A'
    if not _tui_color_on():
        return s
    return ANSI.get(key, '') + s + ANSI['reset']

def _tui_uptime():
    try:
        secs = int(max(0, time.time() - float(TUI.get('started_at') or 0)))
    except Exception:
        secs = 0
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f'{h:02d}:{m:02d}:{s:02d}'

def _tui_int(key):
    try:
        return int(TUI.get(key) or 0)
    except Exception:
        return 0

def _tui_row(label, value):
    return (_tui_paint(str(label).ljust(TUI_LABEL_W), 'label')
            + _tui_paint(':', 'label') + ' '
            + _tui_paint(value, 'value'))

def tui_render():
    try:
        lines = []
        # Block 1: header & status
        lines.append(_tui_paint('MUSE PRO CLI', 'value') + ' ' + _tui_paint('v' + str(VERSION), 'dim'))
        lines.append(_tui_paint('Creator @Noxo', 'value') + _tui_paint(' - Free Community Tool', 'dim'))
        status = str(TUI.get('status') or 'STOPPED')
        status_key = 'ok' if status == 'RUNNING' else 'err'
        lines.append(_tui_paint('Status', 'label') + _tui_paint(':', 'label') + ' '
                     + _tui_paint('> ' + status, status_key)
                     + '    ' + _tui_paint('Uptime', 'label') + _tui_paint(':', 'label') + ' '
                     + _tui_paint(_tui_uptime(), 'value'))
        lines.append('')
        # Block 2: core identity
        lines.append(_tui_paint('Core', 'label'))
        lines.append(_tui_row('Account', TUI.get('account') or 'N/A'))
        lines.append(_tui_row('Reff', TUI.get('reff') or 'N/A'))
        lines.append(_tui_row('Mode', TUI.get('mode') or 'N/A'))
        lines.append(_tui_row('Proxy', TUI.get('proxy') or 'N/A'))
        lines.append(_tui_row('DOB', TUI.get('dob') or 'N/A'))
        lines.append('')
        # Block 3: live metrics
        lines.append(_tui_paint('Metrics', 'label'))
        lines.append(_tui_row('Codes remaining', f'{_tui_int("codes_remaining"):,}'))
        lines.append(_tui_row('Accounts this code', f'{_tui_int("accounts_this_code"):,} / {_tui_int("max_per_code"):,}'))
        lines.append(_tui_row('Accounts saved', f'{_tui_int("accounts_saved"):,}'))
        lines.append(_tui_row('Last code', TUI.get('last_code') or 'N/A'))
        lines.append(_tui_row('Current step', TUI.get('step') or 'idle'))
        lines.append('')
        # Block 4: recent activity
        lines.append(_tui_paint('Activity', 'label'))
        entries = list(LOG_BUF)[-5:]
        if not entries:
            lines.append(_tui_paint('[--:--:--]', 'ts') + ' ' + _tui_paint('>', 'label') + ' ' + _tui_paint('waiting for activity', 'dim'))
        else:
            for ts, step, msg in entries:
                lines.append(_tui_paint(f'[{ts}]', 'ts') + ' ' + _tui_paint('>', 'label') + ' ' + _tui_paint(f'{step}: {msg}', 'value'))
        lines.append('')
        lines.append(_tui_paint('[Ctrl+C to stop]', 'dim'))
        return '\n'.join(line + '\033[K' for line in lines) + '\n'
    except Exception:
        return ''

def tui_flush():
    try:
        out = tui_render()
        if out:
            sys.stdout.write('\033[H' + out + '\033[J')
            sys.stdout.flush()
    except Exception:
        pass

def tui_start(mode='Auto Farm & Recycle'):
    try:
        if not _tui_tty():
            return
        TUI['active'] = True
        TUI['started_at'] = time.time()
        TUI['status'] = 'RUNNING'
        TUI['mode'] = mode
        try:
            LOG_BUF.clear()
        except Exception:
            pass
        sys.stdout.write('\033[?25l')
        sys.stdout.flush()
        tui_flush()
    except Exception:
        pass

def tui_stop():
    try:
        TUI['active'] = False
        TUI['status'] = 'STOPPED'
        if _tui_tty():
            sys.stdout.write('\033[?25h')
            sys.stdout.flush()
    except Exception:
        pass

def tui_log(step, msg):
    try:
        LOG_BUF.append((time.strftime('%H:%M:%S'), str(step), str(msg)))
        if TUI.get('active'):
            tui_flush()
    except Exception:
        pass

def _tui_mask_email(email):
    try:
        e = str(email or '')
        if '@' not in e:
            return e or 'N/A'
        local, _, domain = e.partition('@')
        if len(local) <= 2:
            return local[:1] + '***@' + domain
        return local[:2] + '*' * max(3, len(local) - 2) + '@' + domain
    except Exception:
        return 'N/A'

def banner():
    print(col('MUSE PRO CLI', 'c') + col(f'  v{VERSION}', 'w'))
    print(col('Creator @Noxo - Free Community Tool', 'w'))
    print(col('Target IP must be US - use VPN or proxy (optional)', 'y'))
    print(col('VCC is NOT stored (in-memory only), proxy is saved for reuse', 'w'))
    print(col('Gmail App Password stored in plaintext in .muse_state.json', 'y'))
    print(col('Processed accounts saved to data/output/accounts.txt', 'w'))

def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default
    finally:
        pass

def save_json(path, obj):
    tmp = f'{path}.{os.getpid()}.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(obj, f, indent=1)
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass

def state():
    s = load_json(STATE_FILE, {'proxy': '', 'reff': ''})
    if not isinstance(s, dict):
        s = {'proxy': '', 'reff': ''}
    s.setdefault('proxy', '')
    s.setdefault('reff', '')
    s.setdefault('default_email', '')
    s.setdefault('gmail_app_password', '')
    s.setdefault('invite_file', '')
    s.setdefault('dob', 'random')
    return s

def mask_secret(s):
    s = s or ''
    if len(s) <= 4:
        return '****'
    return s[:4] + '*' * (len(s) - 4)

def startup_config(st):
    print(col('\n⚙️  AUTO FARM SETUP (Press Enter to keep current values)', 'c'))
    try:
        e = ask(f"Default email (Gmail) to recycle [{st.get('default_email') or 'not set'}]", allow_empty=True)
        if e:
            st['default_email'] = e
        p = ask(f"Gmail App Password [{mask_secret(st.get('gmail_app_password')) or 'not set'}]", allow_empty=True)
        if p:
            st['gmail_app_password'] = p
        d_raw = ask(f"Date of birth (YYYY-MM-DD or 'random') [{st.get('dob') or 'random'}]", allow_empty=True)
        if d_raw:
            try:
                st['dob'] = normalize_dob(d_raw)
            except ValueError as ex:
                print(col(f"  ⚠ {ex} — keeping previous ({st.get('dob') or 'random'})", 'y'))
        else:
            st.setdefault('dob', 'random')
        f = ask(f"Path to invite_code.txt [{st.get('invite_file') or INVITE_FILE_DEFAULT}]", allow_empty=True)
        st['invite_file'] = f or st.get('invite_file') or INVITE_FILE_DEFAULT
        save_json(STATE_FILE, st)
        print(col(f"✔ Saved: {st['default_email'] or '(no email)'} • DOB: {st['dob']} • file {st['invite_file']}", 'g'))
    except (EOFError, KeyboardInterrupt):
        print(col('\n(Skipped setup — using saved values)', 'y'))

def ask(q, allow_empty=False):
    while True:
        v = input(col('» ', 'g') + q + ': ').strip()
        if v or allow_empty:
            return v
        print(col('  (empty — please enter a value)', 'y'))

def confirm(q):
    return input(col('» ', 'y') + q + ' ' + col('[y/N]', 'w') + ' ').strip().lower() == 'y'

def menu(st):
    rows = [
        ('Proxy', mask_proxy(st.get('proxy') or '') or 'not set - system/VPN IP (must be US)', bool(st.get('proxy'))),
        ('Reff', st.get('reff') or 'not set - stops after registration', bool(st.get('reff'))),
        ('Default email', st.get('default_email') or 'not set - configure via menu 2', bool(st.get('default_email'))),
        ('DOB', st.get('dob') or 'random', True),
    ]
    print()
    for label, value, ok in rows:
        print(col(label.ljust(13), 'w') + col(': ', 'w') + col(value, 'g' if ok else 'y'))
    print()
    items = [
        ('1', 'Auto Farm & Recycle (Gmail -> Fusion -> Next Code)'),
        ('2', 'Configure Auto Farm (Gmail, app password, DOB, file)'),
        ('3', 'Auto Register Single Account (Fusion Mail + OTP)'),
        ('4', 'VCC Verification Only'),
        ('5', 'Set Proxy (optional)'),
        ('6', 'Set Referral Code (for single register)'),
        ('7', 'Check My IP & Country'),
        ('8', 'Purge Stored Data (reset settings)'),
        ('0', 'Exit'),
    ]
    for key, text in items:
        print(f"  [{col(key, 'b')}] {text}")
    print()

def set_proxy_flow(st):
    p = ask('paste proxy (http://user:pass@host:port / socks5://…)')
    try:
        proxy = parse_proxy(p)
    except ValueError as e:
        print(col(f'❌ {e}', 'r'))
        return
    finally:
        pass
    print(col('🔍 Testing proxy & verifying country (10–20s, 3 attempts)…', 'y'))
    info = geo_check_proxy(proxy)
    ok, verdict = us_verdict(info)
    if not ok:
        print(col(f'❌ {verdict}', 'r'))
        return
    print(col(f'✅ {verdict}', 'g'))
    st['proxy'] = proxy
    save_json(STATE_FILE, st)

def set_reff_flow(st):
    r = ask('your referral code (leave blank = do not use)', allow_empty=True)
    st['reff'] = r
    save_json(STATE_FILE, st)
    print(col(f"✔ Referral code saved: {r or '(empty — register-only)'}", 'g'))

def otp_console(email):
    was_active = bool(TUI.get('active'))
    if was_active:
        try:
            TUI['active'] = False
            if _tui_tty():
                sys.stdout.write('\033[?25h')
                sys.stdout.flush()
        except Exception:
            pass
    try:
        print(col(f'\nOTP sent to {email}', 'b'))
        return ask('enter OTP code')
    finally:
        if was_active:
            try:
                TUI['active'] = True
                if _tui_tty():
                    sys.stdout.write('\033[?25l')
                    sys.stdout.flush()
                tui_flush()
            except Exception:
                pass

def vcc_console(n_try=3):
    was_active = bool(TUI.get('active'))
    if was_active:
        try:
            TUI['active'] = False
            if _tui_tty():
                sys.stdout.write('\033[?25h')
                sys.stdout.flush()
        except Exception:
            pass
    try:
        print(col('\nMuse requires CARD VERIFICATION NOW — continuing run without re-login', 'y'))
        print(col('   VCC is NOT stored — held in session RAM only.', 'w'))
        for i in range(n_try):
            raw = ask(f"VCC format number|MM|YY|CVV (attempt {i + 1}/{n_try}, 'skip' = cancel)", allow_empty=True)
            if raw.lower() in ('skip', 'cancel', 'batal', ''):
                print(col('Verification skipped — stopped cleanly (not an error)', 'y'))
                return None
            try:
                num, exp, cvv = parse_vcc_pipe(raw)
                vcc = {'number': num, 'expiry': exp, 'cvv': cvv, 'zip': zip_random('US'), 'country': 'US'}
                check_vcc(vcc)
                return vcc
            except ValueError as e:
                print(col(f'{e} — please try again', 'r'))
            except SystemExit:
                print(col('Invalid format — example: 5217123456789090|07|27|351', 'r'))
            finally:
                pass
        return None
    finally:
        if was_active:
            try:
                TUI['active'] = True
                if _tui_tty():
                    sys.stdout.write('\033[?25l')
                    sys.stdout.flush()
                tui_flush()
            except Exception:
                pass

def flow_register_cli(email):
    CFG['email'] = email
    api, csrf = start_flow()
    token, _ = _login_common(api, csrf, email)
    if not token:
        fail('auth', 'missing frl_access_token')
    bootstrap_session(api, token)
    ref = CFG.get('ref_code') or ''
    if ref:
        out = _post_auth(api, ref_code=ref)
        log('DONE', f'register {email} -> redeem {ref}: {out}')
    else:
        log('DONE', f'register {email} completed (without redeem — referral code not set)')
    dob_val = CFG.get('dob') or 'random'
    save_account_output(email, code=ref, dob=dob_val)
    return True

def pretty_fail(step, err):
    e = str(err or '').lower()
    if any((k in e for k in ('proxy', 'socks', 'connecttimeout', 'tunnel', '407'))) or 'landing page unreachable' in e:
        print(col('🌐 Proxy unreachable (dead/slow/rejected). Switch to another US proxy and retry.', 'r'))
    elif 'checkout' in e:
        print(col('💳 Server rejected card processing (403/anti-fraud). Try another VCC / US proxy.', 'r'))
    elif '403' in e or 'forbidden' in e or '429' in e:
        print(col('🌐 Server rejected this IP (geo/WAF/rate-limit). Switch to another US proxy.', 'r'))
    elif 'otp' in e:
        print(col('⏳ OTP issue (timed out or invalid email). Retry from menu.', 'r'))
    elif 'card' in e or 'request not completed' in e:
        print(col('💳 Card route rejected. Try another card (Visa/Mastercard).', 'r'))
    else:
        print(col(f'⚠ Failed [{step}]: {str(err)[:300]}', 'r'))

def run_flow(kind, st, email, vcc=None):
    CFG['proxy_enabled'] = bool(st['proxy'])
    CFG['proxy'] = st['proxy']
    PROXIES.clear()
    PROXIES.update(get_proxies(CFG))
    CFG['ref_code'] = st['reff']
    CFG['gmail_password'] = st.get('gmail_app_password') or ''
    CFG['dob'] = st.get('dob') or 'random'
    CFG['vcc'] = vcc or {'number': '', 'expiry': '', 'cvv': '', 'zip': '', 'country': ''}
    print(col(f'\n🚀 {kind.upper()} started… (Ctrl+C = safe stop)\n', 'c'))
    ok = False
    try:
        if kind == 'register':
            ok = flow_register_cli(email)
        elif kind == 'vcconly':
            CFG['email'] = email
            flow_vcc_verify()
            ok = True
    except KeyboardInterrupt:
        print(col('\n🛑 Interrupted by user — engine session dropped', 'y'))
    except SystemExit:
        print(col('❌ Failed (see log details above)', 'r'))
    except Exception as ex:
        pretty_fail('internal', f'{type(ex).__name__}: {ex}')
    finally:
        pass
    if ok and kind == 'register':
        print(col('✅ COMPLETED 🎉', 'g'))

def vcconly_flow(st):
    email = ask('account email')
    pipe = ask('VCC number|MM|YY|CVV')
    try:
        num, exp, cvv = parse_vcc_pipe(pipe)
    except SystemExit:
        print(col('❌ Invalid format', 'r'))
        return
    finally:
        pass
    if card_brand(num) not in ('VISA', 'MASTERCARD'):
        print(col(f'❌ Card brand {card_brand(num)} is not supported (Visa/Mastercard only)', 'r'))
        return
    vcc = {'number': num, 'expiry': exp, 'cvv': cvv, 'zip': zip_random('US'), 'country': 'US'}
    run_flow('vcconly', st, email, vcc)

def purge_all():
    if not confirm('Delete your proxy, settings, and output files?'):
        print(col('Cancelled', 'y'))
        return
    leftovers = []
    for p in (STATE_FILE, os.path.join(BASE_DIR, '.muse_history.json'), ACCOUNTS_OUTPUT_DEFAULT, PENDING_SWAP_FILE, FARM_LOCK_FILE):
        try:
            os.remove(p)
        except FileNotFoundError:
            # Already gone - not a failure.
            pass
        except OSError:
            leftovers.append(p)
        finally:
            pass
    if leftovers:
        print(col(f"⚠ Could not delete: {', '.join(leftovers)}", 'y'))
        return
    print(col('✅ Your data has been cleanly deleted', 'g'))

def cek_ip_flow(st):
    print(col('🌍 Checking external visible IP…', 'y'))
    import curl_cffi
    proxies = {'http': st['proxy'], 'https': st['proxy']} if st['proxy'] else None
    try:
        r = curl_cffi.requests.get('http://ip-api.com/json/?fields=status,country,countryCode,query', proxies=proxies, timeout=15, impersonate='chrome')
        d = r.json()
        via = 'proxy' if proxies else 'system/VPN IP'
        if d.get('status') == 'success':
            cc = (d.get('countryCode') or '').upper()
            tag = col('✔ US — safe to run 🎉', 'g') if cc == 'US' else col(f'⚠ NOT US ({cc}) — change VPN/proxy country!', 'r')
            print(f"   {via}: {col(d.get('query', '?'), 'b')} → {d.get('country', '?')} ({cc})  {tag}")
        else:
            print(col(f'   ip-api rejected: {d}', 'r'))
    except Exception as e:
        print(col(f'❌ {type(e).__name__}: {str(e)[:160]}', 'r'))
    finally:
        pass

def main_cli():
    globals()['OTP_PROVIDER'] = otp_console
    globals()['VCC_PROVIDER'] = vcc_console
    old_log = globals()['log']

    def colored_log(step, msg):
        if TUI.get('active'):
            tui_log(step, msg)
            return
        k = 'w'
        s = str(step).lower()
        if 'error' in s or 'fail' in s or '403' in s:
            k = 'r'
        elif 'vcc' in s or 'otp' in s or 'gate' in s:
            k = 'y'
        elif 'done' in s or 'complete' in s or 'selesai' in s or 'DONE' in str(step):
            k = 'g'
        print(f'   [{col(step, k)}] {str(msg)[:300]}', flush=True)
    globals()['log'] = colored_log
    banner()
    startup_config(state())
    while True:
        st = state()
        menu(st)
        try:
            p = input(col('choose» ', 'g')).strip()
        except (KeyboardInterrupt, EOFError):
            print('\nInterrupted - exiting')
            break
        if p == '1':
            st = state()
            try:
                flow_auto_farm(st)
            except KeyboardInterrupt:
                tui_stop()
                print(col('\nInterrupted by user - auto farm safely stopped', 'y'))
            except SystemExit:
                tui_stop()
                print(col('Auto farm stopped (see log details above)', 'r'))
            except Exception as ex:
                tui_stop()
                pretty_fail('farm', f'{type(ex).__name__}: {ex}')
        elif p == '2':
            startup_config(state())
        elif p == '3':
            if not st['proxy']:
                print(col('i  No proxy set — using system/VPN IP (Must be US! Check menu 7)', 'y'))
            print(col('⏎ Enter = auto-generate Fusion temp mail | type email manually if desired', 'w'))
            ans = ask('email (blank = auto Fusion)', allow_empty=True)
            try:
                email = fusion_new_email() if not ans else ans
                run_flow('register', st, email)
            except KeyboardInterrupt:
                tui_stop()
                print(col('\nInterrupted by user - register stopped', 'y'))
            except SystemExit:
                tui_stop()
                print(col('Register stopped (see log details above)', 'r'))
        elif p == '4':
            vcconly_flow(st)
        elif p == '5':
            set_proxy_flow(st)
        elif p == '6':
            set_reff_flow(st)
        elif p == '7':
            cek_ip_flow(st)
        elif p == '8':
            purge_all()
        elif p == '0':
            print(col('Goodbye - wish you success! @Noxo', 'c'))
            break
        else:
            print(col('Invalid option', 'y'))
if __name__ == '__main__':
    main_cli()