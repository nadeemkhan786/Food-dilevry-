import base64
import collections
from datetime import datetime
from datetime import timedelta
import functools
import hashlib
import hmac
import re
import requests
import random
import time

import googlemaps

try: # Python 3
    from urllib.parse import urlencode
except ImportError: # Python 2
    from urllib import urlencode


_USER_AGENT = "GoogleGeoApiClientPython/%s" % googlemaps.__version__
_DEFAULT_BASE_URL = "https://maps.googleapis.com"

_RETRIABLE_STATUSES = set([500, 503, 504])

class Client(object):
    

    def __init__(self, key=None, client_id=None, client_secret=None,
                 timeout=None, connect_timeout=None, read_timeout=None,
                 retry_timeout=60, requests_kwargs=None,
                 queries_per_second=50, channel=None,
                 retry_over_query_limit=True):
        
        if not key and not (client_secret and client_id):
            raise ValueError("Must provide API key or enterprise credentials "
                             "when creating client.")

        if key and not key.startswith("AIza"):
            raise ValueError("Invalid API key provided.")

        if channel:
            if not client_id:
                raise ValueError("The channel argument must be used with a "
                                 "client ID")
            if not re.match("^[a-zA-Z0-9._-]*$", channel):
                raise ValueError("The channel argument must be an ASCII "
                    "alphanumeric string. The period (.), underscore (_)"
                    "and hyphen (-) characters are allowed.")

        self.session = requests.Session()
        self.key = key

        if timeout and (connect_timeout or read_timeout):
            raise ValueError("Specify either timeout, or connect_timeout "
                             "and read_timeout")

        if connect_timeout and read_timeout:
            # Check that the version of requests is >= 2.4.0
            chunks = requests.__version__.split(".")
            if int(chunks[0]) < 2 or (int(chunks[0]) == 2 and int(chunks[1]) < 4):
                raise NotImplementedError("Connect/Read timeouts require "
                                          "requests v2.4.0 or higher")
            self.timeout = (connect_timeout, read_timeout)
        else:
            self.timeout = timeout

        self.client_id = client_id
        self.client_secret = client_secret
        self.channel = channel
        self.retry_timeout = timedelta(seconds=retry_timeout)
        self.requests_kwargs = requests_kwargs or {}
        headers = self.requests_kwargs.pop('headers', {})
        headers.update({"User-Agent": _USER_AGENT})        
        self.requests_kwargs.update({
            "headers": headers,
            "timeout": self.timeout,
            "verify": True,  # NOTE(cbro): verify SSL certs.
        })

        self.queries_per_second = queries_per_second
        self.retry_over_query_limit = retry_over_query_limit
        self.sent_times = collections.deque("", queries_per_second)

    def _request(self, url, params, first_request_time=None, retry_counter=0,
             base_url=_DEFAULT_BASE_URL, accepts_clientid=True,
             extract_body=None, requests_kwargs=None, post_json=None):
        
    

        if not first_request_time:
            first_request_time = datetime.now()

        elapsed = datetime.now() - first_request_time
        if elapsed > self.retry_timeout:
            raise googlemaps.exceptions.Timeout()

        if retry_counter > 0:
            # 0.5 * (1.5 ^ i) is an increased sleep time of 1.5x per iteration,
            # starting at 0.5s when retry_counter=0. The first retry will occur
            # at 1, so subtract that first.
            delay_seconds = 0.5 * 1.5 ** (retry_counter - 1)

            # Jitter this value by 50% and pause.
            time.sleep(delay_seconds * (random.random() + 0.5))

        authed_url = self._generate_auth_url(url, params, accepts_clientid)

        # Default to the client-level self.requests_kwargs, with method-level
        # requests_kwargs arg overriding.
        requests_kwargs = requests_kwargs or {}
        final_requests_kwargs = dict(self.requests_kwargs, **requests_kwargs)

        # Determine GET/POST.
        requests_method = self.session.get
        if post_json is not None:
            requests_method = self.session.post
            final_requests_kwargs["json"] = post_json

        try:
            response = requests_method(base_url + authed_url,
                                       **final_requests_kwargs)
        except requests.exceptions.Timeout:
            raise googlemaps.exceptions.Timeout()
        except Exception as e:
            raise googlemaps.exceptions.TransportError(e)

        if response.status_code in _RETRIABLE_STATUSES:
            # Retry request.
            return self._request(url, params, first_request_time,
                                 retry_counter + 1, base_url, accepts_clientid,
                                 extract_body, requests_kwargs, post_json)

        # Check if the time of the nth previous query (where n is
        # queries_per_second) is under a second ago - if so, sleep for
        # the difference.
        if self.sent_times and len(self.sent_times) == self.queries_per_second:
            elapsed_since_earliest = time.time() - self.sent_times[0]
            if elapsed_since_earliest < 1:
                time.sleep(1 - elapsed_since_earliest)

        try:
            if extract_body:
                result = extract_body(response)
            else:
                result = self._get_body(response)
            self.sent_times.append(time.time())
            return result
        except googlemaps.exceptions._RetriableRequest as e:
            if isinstance(e, googlemaps.exceptions._OverQueryLimit) and not self.retry_over_query_limit:
                raise

            # Retry request.
            return self._request(url, params, first_request_time,
                                 retry_counter + 1, base_url, accepts_clientid,
                                 extract_body, requests_kwargs, post_json)

    def _get(self, *args, **kwargs):  # Backwards compatibility.
        return self._request(*args, **kwargs)

    def _get_body(self, response):
        if response.status_code != 200:
            raise googlemaps.exceptions.HTTPError(response.status_code)

        body = response.json()

        api_status = body["status"]
        if api_status == "OK" or api_status == "ZERO_RESULTS":
            return body

        if api_status == "OVER_QUERY_LIMIT":
            raise googlemaps.exceptions._OverQueryLimit(
                api_status, body.get("error_message"))

        raise googlemaps.exceptions.ApiError(api_status,
                                             body.get("error_message"))

    def _generate_auth_url(self, path, params, accepts_clientid):
        """Returns the path and query string portion of the request URL, first
        adding any necessary parameters.
        :param path: The path portion of the URL.
        :type path: string
        :param params: URL parameters.
        :type params: dict or list of key/value tuples
        :rtype: string
        """
        # Deterministic ordering through sorting by key.
        # Useful for tests, and in the future, any caching.
        extra_params = getattr(self, "_extra_params", None) or {}
        if type(params) is dict:
            params = sorted(dict(extra_params, **params).items())
        else:
            params = sorted(extra_params.items()) + params[:] # Take a copy.

        if accepts_clientid and self.client_id and self.client_secret:
            if self.channel:
                params.append(("channel", self.channel))
            params.append(("client", self.client_id))

            path = "?".join([path, urlencode_params(params)])
            sig = sign_hmac(self.client_secret, path)
            return path + "&signature=" + sig

        if self.key:
            params.append(("key", self.key))
            return path + "?" + urlencode_params(params)

        raise ValueError("Must provide API key for this API. It does not accept "
                         "enterprise credentials.")


from googlemaps.directions import directions
from googlemaps.distance_matrix import distance_matrix
from googlemaps.elevation import elevation
from googlemaps.elevation import elevation_along_path
from googlemaps.geocoding import geocode
from googlemaps.geocoding import reverse_geocode
from googlemaps.geolocation import geolocate
from googlemaps.timezone import timezone
from googlemaps.roads import snap_to_roads
from googlemaps.roads import nearest_roads
from googlemaps.roads import speed_limits
from googlemaps.roads import snapped_speed_limits
from googlemaps.places import find_place
from googlemaps.places import places
from googlemaps.places import places_nearby
from googlemaps.places import places_radar
from googlemaps.places import place
from googlemaps.places import places_photo
from googlemaps.places import places_autocomplete
from googlemaps.places import places_autocomplete_query


def make_api_method(func):
    
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        args[0]._extra_params = kwargs.pop("extra_params", None)
        result = func(*args, **kwargs)
        try:
            del args[0]._extra_params
        except AttributeError:
            pass
        return result
    return wrapper


Client.directions = make_api_method(directions)
Client.distance_matrix = make_api_method(distance_matrix)
Client.elevation = make_api_method(elevation)
Client.elevation_along_path = make_api_method(elevation_along_path)
Client.geocode = make_api_method(geocode)
Client.reverse_geocode = make_api_method(reverse_geocode)
Client.geolocate = make_api_method(geolocate)
Client.timezone = make_api_method(timezone)
Client.snap_to_roads = make_api_method(snap_to_roads)
Client.nearest_roads = make_api_method(nearest_roads)
Client.speed_limits = make_api_method(speed_limits)
Client.snapped_speed_limits = make_api_method(snapped_speed_limits)
Client.find_place = make_api_method(find_place)
Client.places = make_api_method(places)
Client.places_nearby = make_api_method(places_nearby)
Client.places_radar = make_api_method(places_radar)
Client.place = make_api_method(place)
Client.places_photo = make_api_method(places_photo)
Client.places_autocomplete = make_api_method(places_autocomplete)
Client.places_autocomplete_query = make_api_method(places_autocomplete_query)


def sign_hmac(secret, payload):
    
    payload = payload.encode('ascii', 'strict')
    secret = secret.encode('ascii', 'strict')
    sig = hmac.new(base64.urlsafe_b64decode(secret), payload, hashlib.sha1)
    out = base64.urlsafe_b64encode(sig.digest())
    return out.decode('utf-8')


def urlencode_params(params):
    
    
    params = [(key, normalize_for_urlencode(val)) for key, val in params]
    
    return requests.utils.unquote_unreserved(urlencode(params))


try:
    unicode
    

    def normalize_for_urlencode(value):
        
        if isinstance(value, unicode):
            return value.encode('utf8')

        if isinstance(value, str):
            return value

        return normalize_for_urlencode(str(value))

except NameError:
    def normalize_for_urlencode(value):
       
        
        return value
