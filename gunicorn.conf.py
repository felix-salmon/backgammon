# Auto-loaded by `gunicorn app:app`. Start background threads in each worker
# after the fork, never in the arbiter.
def post_fork(server, worker):
    import app as _app
    _app.ensure_background_threads()
