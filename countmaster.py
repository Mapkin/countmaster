# vim: set fileencoding=utf-8

import redis

from datetime import date
from flask import Flask, json, make_response, request, url_for
from flask_gzip import Gzip
from flask_heroku import Heroku


###
### Flask application object.
###
app = Flask(__name__)
app.debug = True

# Bootstrap Heroku environment variables.
heroku = Heroku(app)

# Enable gzip compression of large responses.
gzip = Gzip(app, compress_level=9)


###
### Utils.
###
def make_json_response(response, status=200):
    """Return a pretty-printed JSON response.

    Similar to Flask's ``jsonify`` function, but with pretty-printing,
    UTF-8, and custom headers.

    """
    resp = make_response(json.dumps(response,
                                    sort_keys=True,
                                    indent=4,
                                    ensure_ascii=False), status)
    resp.headers['Content-Type'] = "application/json; charset=utf-8"
    resp.headers['Cache-Control'] = "no-cache, no-store"
    return resp


def redis_init(db=0, max_connections=1):
    """Create a Redis connection.

    Redis will set up a connection pooler automatically, but the nano
    tier of Redis To Go only supports 10 concurrent connections.

    """
    pool = redis.ConnectionPool(host=app.config['REDIS_HOST'],
                                port=app.config['REDIS_PORT'],
                                password=app.config['REDIS_PASSWORD'],
                                db=db,
                                max_connections=max_connections)
    return redis.Redis(connection_pool=pool)


###
### Global Redis connection.
###
app.redis = redis_init()


###
### Hooks.
###


@app.before_request
def force_ssl():
    """Throw an error if the request is not secure."""
    criteria = [
        app.debug,
        request.is_secure,
        request.headers.get('X-Forwarded-Proto', "http") == "https",
    ]

    if not any(criteria):
        return make_json_response({
            'type': "invalid_request_error",
            'message': "This API is only accessible over HTTPS."
        }, 403)


@app.before_request
def authenticate_client():
    """Ensure that the client has a valid API key."""
    auth = request.authorization
    data = None

    if not auth:
        data = {
            'type': "invalid_request_error",
            'message': ("You did not provide an API key. You must provide "
                        "your API key in then Authorization header using "
                        "Basic auth (e.g. 'Authorization: Basic "
                        "YOUR_SECRET_KEY')."),
        }

    if not app.redis.sismember("api_keys", auth.username):
        data = {
            'type': "invalid_request_error",
            'message': "Invalid API key provided: {}".format(auth.username)
        }

    if data:
        response = make_json_response(data, 401)
        response.headers['WWW-Authenticate'] = 'Basic realm="Mapkin"'
        return response


###
### Routes.
###


@app.route("/api/v1/counters")
def get_counters():
    """Returns the collection of registered counters.

    :rtype: json
    :returns: A dictionary that maps a counter resource to its relative URI.

    Usage::

    $ curl -u YOUR_API_KEY: /api/v1/counters
    {
        "counter0": /api/v1/counters/counter0,
        "counter1": /api/v1/counters/counter1,
        ...
    }

    """
    counters = app.redis.smembers('counters')
    map = {c: url_for("get_counter", counter=c) for c in counters}

    return make_json_response(map)


@app.route("/api/v1/counters/<counter>")
def get_counter(counter):
    """Get the specified counter's value for today.

    :rtype: json
    :returns: The counter's current value for today.

    Usage::

        $ curl -u YOUR_API_KEY: /api/v1/counters/YOUR_COUNTER
        {
            'count': 5,
            'date': "2013-06-06"
        }

    """
    today = date.today().isoformat()

    count = app.redis.hget(counter.lower(), today) or 0
    response = {
        'count': long(count),
        'date': today,
    }
    return make_json_response(response)


@app.route("/api/v1/counters/<counter>", methods=["POST"])
def create_counter(counter):
    """Create a new counter.

    :rtype: json
    :returns: A status code of 201 CREATED on success, along with the
        initial representation of the counter. If ``counter`` already
        exists, we will return 409 CONFLICT.

    Usage::

        $ curl -u YOUR_API_KEY -X POST /api/v1/counters/test
        {
            "count": 0,
            "date": TODAY
        }

        $ curl -u YOUR_API_KEY -X POST /api/v1/counters/test
        {
            "message": "A counter named 'test' already exists.",
            "type": "api_error"
        }

    """
    key = counter.lower()

    # Return an error if the key already exists.
    if app.redis.sismember("counters", key):
        return make_json_response({
            'type': "api_error",
            'message': "A counter named '{}' already exists.".format(counter),
        }, 409)

    # Add the counter to the set of counters. Redis will create & increment it
    # for us when the user updates the value. Return its initial
    # representation, which is 0 for today.
    app.redis.sadd("counters", key)
    data = {
        'count': 0,
        'date': date.today().isoformat(),
    }
    response = make_json_response(data, 201)

    # Cook up a 'Location' header with the resource's absolute URI.
    uri = "https://{}{}".format(request.headers['HOST'],
                                url_for("get_counter", counter=counter))
    response.headers['Location'] = uri

    return response


@app.route("/api/v1/counters/<counter>", methods=["PATCH"])
def increment_counter(counter):
    """Increment ``counter`` by one for today.

    :rtype: json
    :returns: A status code of 200 OK on success, along with
        the updated count.

    Usage::

        $ curl -u YOUR_API_KEY -X PATCH /api/v1/counters/test
        {
            'count': 1,
            'date': 2013-06-08
        }

    """
    today = date.today().isoformat()

    # If the counter does not exist, add it to the counters set.
    key = counter.lower()
    if not app.redis.exists(key):
        app.redis.sadd('counters', key)

    # Increment the count for today and return.
    count = app.redis.hincrby(key, today, 1)
    response = {
        'count': count,
        'date': today,
    }
    return make_json_response(response)


if __name__ == "__main__":
    app.run()
