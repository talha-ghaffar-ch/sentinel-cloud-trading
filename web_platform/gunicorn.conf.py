# gunicorn.conf.py — Production config for Sentinel Web Platform
bind             = "127.0.0.1:5000"
workers          = 3          # 2 x CPU cores + 1 is standard
worker_class     = "sync"
timeout          = 60
keepalive        = 5
max_requests     = 1000       # Recycle workers to prevent memory leaks
max_requests_jitter = 100
accesslog        = "/var/log/sentinel-access.log"
errorlog         = "/var/log/sentinel-error.log"
loglevel         = "info"
