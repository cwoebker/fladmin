import os
import os.path as op
import urlparse
import shutil
import re

from collections import defaultdict
from operator import itemgetter

from flask import flash, redirect, Response, request, \
                    Blueprint, render_template, url_for, \
                    abort
from werkzeug import secure_filename

from flask.ext.admin import form
from flask.ext import wtf

from functools import wraps
import datetime
try:
    import json
except:
    import simplejson as json

from stats import RedisMonitor

admin = Blueprint('admin', __name__, template_folder='templates', static_folder='static')

TYPE_TEMPLATES = {
    'hash': 'redis/browse/types/hash.html'
}

conn = None
redis_monitor = None


def setup(connection, url):
    global conn
    conn = connection
    global redis_monitor
    redis_monitor = RedisMonitor([url])

########
# AUTH #
########


def check_auth(username, password):
    """This function is called to check if a username /
    password combination is valid.
    """
    return username == 'admin' and password == 'worcester'


def authenticate():
    """Sends a 401 response that enables basic auth"""
    return Response(
    'Could not verify your access level for that URL.\n'
    'You have to login with proper credentials', 401,
    {'WWW-Authenticate': 'Basic realm="Login Required"'})


def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated

#######
# END #
#######


@admin.route('/')
@requires_auth
def index():
    return render_template('admin.html')

#########
# REDIS #
#########


@admin.route('/redis')
@requires_auth
def redis():
    stats = redis_monitor.getStats()
    return render_template('redis/index.html', stats=stats)


@admin.route('/redis/wipe')
@requires_auth
def wipe():
    conn.flushdb()
    flash("Database cleared.", 'info')
    return redirect('/admin/redis')


@admin.route('/redis/delete')
@requires_auth
def redis_del():
    key = request.args.get('k')
    key_type = key and conn.type(key)
    if key:
        if key_type != 'none':
            conn.delete(key)
    return redirect(url_for('admin.browse'))


@admin.route('/redis/browse')
@requires_auth
def browse():
    stats = redis_monitor.getStats()
    keys_browse = {}
    for key_b in conn.keys():
        try:
            keys_browse[conn.type(key_b)]
        except KeyError:
            keys_browse[conn.type(key_b)] = []
        keys_browse[conn.type(key_b)].append(key_b)

    data = None
    key = request.args.get('k')
    key_type = key and conn.type(key)  # don't call redis without a key
    if key:
        if key_type == 'none':  # bogus key, possibly bad paramenter
            return redirect(url_for('admin.browse'))

        data = {
            'hash': lambda x: conn.hgetall(x),
            'list': lambda x: conn.lrange(x, 0, -1),
            'string': lambda x: [conn.get(x)],
            'zset': lambda x: conn.zrange(x, 0, -1, withscores=True),
            'set': lambda x: conn.smembers(x)

        }[key_type](key)

    return render_template(TYPE_TEMPLATES.get(key_type, 'redis/browse/index.html'),
        data=data,
        _key=key,
        keys_browse=keys_browse,
        stat=stats[0])


# ajax view (json)
@admin.route('/redis/ajax')
@requires_auth
def ajax():
    stats = redis_monitor.getStats(True)
    datetimeHandler = lambda obj: obj.isoformat() if isinstance(obj, datetime.datetime) else None
    return json.dumps(stats, default=datetimeHandler)

#########
# FILES #
#########

settings = {
    'upload': True,
    'delete': True,
    'delete_dirs': True,
    'mkdir': True,
    'rename': True,
}
BASE_URL = '/admin/files/b/'
BASE_PATH = op.dirname(op.dirname(__file__))
FILENAMES = ('swf', 'jpg', 'gif', 'png')


class NameForm(form.BaseForm):
    """
Form with a filename input field.

Validates if provided name is valid for *nix and Windows systems.
"""
    name = wtf.TextField()

    regexp = re.compile(r'^(?!^(PRN|AUX|CLOCK\$|NUL|CON|COM\d|LPT\d|\..*)(\..+)?$)[^\x00-\x1f\\?*:\";|/]+$')

    def validate_name(self, field):
        if not self.regexp.match(field.data):
            raise wtf.ValidationError('Invalid directory name')


class UploadForm(form.BaseForm):
    """
File upload form. Works with FileAdmin instance to check if it is allowed
to upload file with given extension.
"""
    upload = wtf.FileField('File to upload')

    def validate_upload(self, field):
        if not self.upload.has_file():
            raise wtf.ValidationError('File required.')

        filename = self.upload.data.filename
        ext = op.splitext(filename)[1].lower()

        if ext.startswith('.'):
            ext = ext[1:]

        if not ext in FILENAMES:
            raise wtf.ValidationError('Invalid file type.')


def save_file(path, file_data):
    """
Save uploaded file to the disk

`path`
Path to save to
`file_data`
Werkzeug `FileStorage` object
"""
    file_data.save(path)


def is_in_folder(base_path, directory):
        """
Verify if `directory` is in `base_path` folder
"""
        return op.normpath(directory).startswith(base_path)


def _get_dir_url(endpoint, path, **kwargs):
    """
Return prettified URL

`endpoint`
Endpoint name
`path`
Directory path
`kwargs`
Additional arguments
"""
    if not path:
        return url_for(endpoint)
    else:
        #if self._on_windows:
        #    path = path.replace('\\', '/')

        kwargs['path'] = path

        return url_for(endpoint, **kwargs)


def _get_file_url(path):
    """
Return static file url

`path`
Static file path
"""
    return urlparse.urljoin(BASE_URL, path)


def _normalize_path(path):
    """
Verify and normalize path.

If path is not relative to the base directory, will throw 404 exception.

If path does not exist, will also throw 404 exception.
"""
    if path is None:
        directory = BASE_PATH
        path = ''
    else:
        path = op.normpath(path)
        directory = op.normpath(op.join(BASE_PATH, path))

        if not is_in_folder(BASE_PATH, directory):
            abort(404)

    if not op.exists(directory):
        abort(404)

    return BASE_PATH, directory, path


@admin.route('/files')
@admin.route('/files/b/<path>')
@requires_auth
def files(path=None):
    # Get path and verify if it is valid
    base_path, directory, path = _normalize_path(path)

    # Get directory listing
    items = []

    # Parent directory
    if directory != base_path:
        parent_path = op.normpath(op.join(path, '..'))
        if parent_path == '.':
            parent_path = None

        items.append(('..', parent_path, True, 0))

    for f in os.listdir(directory):
        fp = op.join(directory, f)

        items.append((f, op.join(path, f), op.isdir(fp), op.getsize(fp)))

    # Sort by type
    items.sort(key=itemgetter(2), reverse=True)

    # Generate breadcrumbs
    accumulator = ''
    breadcrumbs = [(n, op.join(accumulator, n)) for n in path.split(os.sep)]

    return render_template(
        'files/list.html',
        dir_path=path,
        breadcrumbs=breadcrumbs,
        get_dir_url=_get_dir_url,
        get_file_url=_get_file_url,
        items=items,
        settings=settings
    )


@admin.route('/files/upload/', methods=('GET', 'POST'))
@admin.route('/files/upload/<path:path>', methods=('GET', 'POST'))
def upload(path=None):
    """
Upload view method

`path`
Optional directory path. If not provided, will use base directory
"""
    # Get path and verify if it is valid
    base_path, directory, path = _normalize_path(path)

    if not settings['upload']:
        flash('File uploading is disabled.', 'error')
        return redirect(_get_dir_url('.files', path))

    form = UploadForm()
    if form.validate_on_submit():
        filename = op.join(directory, secure_filename(form.upload.data.filename))

        if op.exists(filename):
            flash('File "%s" already exists.' % form.upload.data.filename, 'error')
        else:
            try:
                save_file(filename, form.upload.data)
                return redirect(_get_dir_url('.files', path))
            except Exception, ex:
                flash('Failed to save file: %s' % ex, 'error')

    return render_template('files/form.html', form=form)


@admin.route('/files/mkdir/', methods=('GET', 'POST'))
@admin.route('/files/mkdir/<path:path>', methods=('GET', 'POST'))
def mkdir(path=None):
    """
Directory creation view method

`path`
Optional directory path. If not provided, will use base directory
"""
    # Get path and verify if it is valid
    base_path, directory, path = _normalize_path(path)

    dir_url = _get_dir_url('.index', path)

    if not settings['mkdir']:
        flash('Directory creation is disabled.', 'error')
        return redirect(dir_url)

    form = NameForm(request.form)

    if form.validate_on_submit():
        try:
            os.mkdir(op.join(directory, form.name.data))
            return redirect(dir_url)
        except Exception, ex:
            flash('Failed to create directory: %s' % ex, 'error')

    return render_template('files/form.html',
                       form=form,
                       dir_url=dir_url)


@admin.route('/files/delete/', methods=('POST',))
def delete():
    """
Delete view method
"""
    path = request.form.get('path')

    if not path:
        return redirect(url_for('.index'))

    # Get path and verify if it is valid
    base_path, full_path, path = _normalize_path(path)

    return_url = _get_dir_url('.files', op.dirname(path))

    if not settings['delete']:
        flash('Deletion is disabled.')
        return redirect(return_url)

    if op.isdir(full_path):
        if not settings['delete_dirs']:
            flash('Directory deletion is disabled.')
            return redirect(return_url)

        try:
            shutil.rmtree(full_path)
            flash('Directory "%s" was successfully deleted.' % path)
        except Exception, ex:
            flash('Failed to delete directory: %s' % ex, 'error')
    else:
        try:
            os.remove(full_path)
            flash('File "%s" was successfully deleted.' % path)
        except Exception, ex:
            flash('Failed to delete file: %s' % ex, 'error')

    return redirect(return_url)


@admin.route('/files/rename/', methods=('GET', 'POST'))
def rename():
    """
Rename view method
"""
    path = request.args.get('path')

    if not path:
        return redirect(url_for('.files'))

    base_path, full_path, path = _normalize_path(path)

    return_url = _get_dir_url('.files', op.dirname(path))

    if not settings['rename']:
        flash('Renaming is disabled.')
        return redirect(return_url)

    if not op.exists(full_path):
        flash('Path does not exist.')
        return redirect(return_url)

    form = NameForm(request.form, name=op.basename(path))
    if form.validate_on_submit():
        try:
            dir_base = op.dirname(full_path)
            filename = secure_filename(form.name.data)

            os.rename(full_path, op.join(dir_base, filename))
            flash('Successfully renamed "%(src)s" to "%(dst)s"',
                  src=op.basename(path),
                  dst=filename)
        except Exception, ex:
            flash('Failed to rename: %s' % ex, 'error')

        return redirect(return_url)

    return render_template('files/rename.html',
                       form=form,
                       path=op.dirname(path),
                       name=op.basename(path),
                       dir_url=return_url)
