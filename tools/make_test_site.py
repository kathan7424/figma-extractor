#!/usr/bin/env python3
"""Creates an ISOLATED test WordPress + Elementor (free) + Hello theme, served by PHP's built-in server.
Never touches your real sites. Uses WAMP's MySQL (root, no password) with a NEW database `fte_test`.

  python tools/make_test_site.py [--dir F:\\wp-fte-test] [--zip C:\\Users\\HP\\Downloads\\wordpress-7.0.zip] [--port 8088]
Then:  php -S 127.0.0.1:8088 -t F:\\wp-fte-test      (or use tools/start_test_site.bat)
Admin: admin / fte-test-pass  (test site only).
"""
from __future__ import annotations

import argparse
import io
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

PHP_CANDIDATES = [r'C:\wamp64\bin\php\php8.3.14\php.exe', r'C:\wamp64\bin\php\php8.2.26\php.exe', 'php']


def find_php():
    for p in PHP_CANDIDATES:
        try:
            subprocess.run([p, '-v'], capture_output=True, check=True)
            return p
        except Exception:
            continue
    raise SystemExit('PHP nathi malyu (WAMP PHP 8.x joiye)')


def safe_extract(z: zipfile.ZipFile, dest: Path, strip=0):
    root = dest.resolve()
    for info in z.infolist():
        parts = Path(info.filename).parts[strip:]
        if not parts:
            continue
        target = dest.joinpath(*parts).resolve()
        if not target.is_relative_to(root):
            raise ValueError('unsafe zip path: ' + info.filename)
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as s, open(target, 'wb') as d:
                shutil.copyfileobj(s, d)


def download(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'fte-studio-test-setup'})
    with urllib.request.urlopen(req, timeout=180) as r:
        return r.read()


INSTALL_PHP = r"""<?php
define('WP_INSTALLING', true);
require '__DIR__/wp-load.php';
require_once ABSPATH . 'wp-admin/includes/upgrade.php';
if (!is_blog_installed()) {
    $r = wp_install('FTE Test Site', 'admin', 'admin@example.test', true, '', 'fte-test-pass', 'en_US');
    echo "installed user id " . $r['user_id'] . "\n";
} else { echo "already installed\n"; }
update_option('permalink_structure', '/%postname%/');
"""

ACTIVATE_PHP = r"""<?php
require '__DIR__/wp-load.php';
require_once ABSPATH . 'wp-admin/includes/plugin.php';
wp_set_current_user(1);
switch_theme('hello-elementor');
foreach (__PLUGINS__ as $p) {
    $r = activate_plugin($p);
    echo $p . ': ' . (is_wp_error($r) ? 'ERROR ' . $r->get_error_message() : 'active') . "\n";
}
flush_rewrite_rules();
echo 'elementor ' . (defined('ELEMENTOR_VERSION') ? ELEMENTOR_VERSION : 'not loaded in this request') . "\n";
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', default=r'F:\wp-fte-test')
    ap.add_argument('--zip', default=str(Path.home() / 'Downloads' / 'wordpress-7.0.zip'))
    ap.add_argument('--port', type=int, default=8088)
    ap.add_argument('--acf-from', default=r'C:\wamp64\www\wpnew\wp-content\plugins\advanced-custom-fields-pro-main',
                    help='optional: copy your licensed ACF PRO from another site for local testing')
    a = ap.parse_args()
    site = Path(a.dir)
    php = find_php()
    if not (site / 'wp-load.php').exists():
        print('extracting WordPress ...')
        site.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(a.zip) as z:
            safe_extract(z, site, strip=1)
    # DB
    db_php = site / '_mkdb.php'
    db_php.write_text("<?php mysqli_report(MYSQLI_REPORT_OFF); $c=mysqli_connect('127.0.0.1','root',''); "
                      "echo mysqli_query($c,'CREATE DATABASE IF NOT EXISTS fte_test CHARACTER SET utf8mb4') ? \"db ok\\n\" : mysqli_error($c);", 'utf-8')
    print(subprocess.run([php, str(db_php)], capture_output=True, text=True).stdout.strip())
    db_php.unlink()
    (site / 'wp-config.php').write_text(f"""<?php
define('DB_NAME', 'fte_test'); define('DB_USER', 'root'); define('DB_PASSWORD', ''); define('DB_HOST', '127.0.0.1');
define('DB_CHARSET', 'utf8mb4'); define('DB_COLLATE', '');
define('AUTH_KEY','a1');define('SECURE_AUTH_KEY','a2');define('LOGGED_IN_KEY','a3');define('NONCE_KEY','a4');
define('AUTH_SALT','a5');define('SECURE_AUTH_SALT','a6');define('LOGGED_IN_SALT','a7');define('NONCE_SALT','a8');
$table_prefix = 'wp_';
define('WP_DEBUG', true); define('WP_DEBUG_DISPLAY', false); define('WP_DEBUG_LOG', true);
define('WP_HOME', 'http://127.0.0.1:{a.port}'); define('WP_SITEURL', 'http://127.0.0.1:{a.port}');
define('WP_ENVIRONMENT_TYPE', 'local'); define('DISABLE_WP_CRON', true); define('FS_METHOD', 'direct');
if (!defined('ABSPATH')) define('ABSPATH', __DIR__ . '/');
require_once ABSPATH . 'wp-settings.php';
""", 'utf-8')
    plugins = site / 'wp-content' / 'plugins'
    themes = site / 'wp-content' / 'themes'
    if not (plugins / 'elementor').exists():
        print('downloading Elementor (free) ...')
        safe_extract(zipfile.ZipFile(io.BytesIO(download('https://downloads.wordpress.org/plugin/elementor.latest-stable.zip'))), plugins)
    if not (themes / 'hello-elementor').exists():
        print('downloading Hello Elementor theme ...')
        safe_extract(zipfile.ZipFile(io.BytesIO(download('https://downloads.wordpress.org/theme/hello-elementor.latest-stable.zip'))), themes)
    use = ['elementor/elementor.php']
    acf = Path(a.acf_from)
    if acf.exists() and not (plugins / 'acf-pro').exists():
        print('copying ACF PRO for local testing ...')
        shutil.copytree(acf, plugins / 'acf-pro', ignore=shutil.ignore_patterns('.git', 'node_modules'))
    if (plugins / 'acf-pro' / 'acf.php').exists():
        use.append('acf-pro/acf.php')
    bridge = Path(__file__).resolve().parent.parent / 'wp-plugin' / 'fte-studio-bridge'
    if bridge.exists():
        shutil.copytree(bridge, plugins / 'fte-studio-bridge', dirs_exist_ok=True)
        use.append('fte-studio-bridge/fte-studio-bridge.php')
    (site / '_install.php').write_text(INSTALL_PHP.replace('__DIR__', site.as_posix()), 'utf-8')
    print(subprocess.run([php, str(site / '_install.php')], capture_output=True, text=True).stdout.strip())
    (site / '_activate.php').write_text(ACTIVATE_PHP.replace('__DIR__', site.as_posix()).replace('__PLUGINS__', repr(use).replace("'", '"').replace('[', 'array(').replace(']', ')')), 'utf-8')
    print(subprocess.run([php, str(site / '_activate.php')], capture_output=True, text=True).stdout.strip())
    for f in ('_install.php', '_activate.php'):
        (site / f).unlink(missing_ok=True)
    print(f'\nReady. Start:  {php} -S 127.0.0.1:{a.port} -t {site}')


if __name__ == '__main__':
    main()
