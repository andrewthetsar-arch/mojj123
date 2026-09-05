
import sys

import os



BASE_DIR = '/home/c/cc361963/public_html'

if BASE_DIR not in sys.path:

    sys.path.insert(0, BASE_DIR)



user_site = '/home/c/cc361963/.local/lib/python3.10/site-packages'

if user_site not in sys.path:

    sys.path.insert(0, user_site)



from a2wsgi import ASGIMiddleware

from app import app



application = ASGIMiddleware(app)

