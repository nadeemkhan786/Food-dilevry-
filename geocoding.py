from googlemaps import convert


def geocode(client, address=None, components=None, bounds=None, region=None,
            language=None):
    

    params = {}

    if address:
        params["address"] = address

    if components:
        params["components"] = convert.components(components)

    if bounds:
        params["bounds"] = convert.bounds(bounds)

    if region:
        params["region"] = region

    if language:
        params["language"] = language

    return client._request("/maps/api/geocode/json", params).get("results", [])


def reverse_geocode(client, latlng, result_type=None, location_type=None,
                    language=None):
   

    # Check if latlng param is a place_id string.
    #  place_id strings do not contain commas; latlng strings do.
    if convert.is_string(latlng) and ',' not in latlng:
        params = {"place_id": latlng}
    else:
        params = {"latlng": convert.latlng(latlng)}

    if result_type:
        params["result_type"] = convert.join_list("|", result_type)

    if location_type:
        params["location_type"] = convert.join_list("|", location_type)

    if language:
        params["language"] = language

    return client._request("/maps/api/geocode/json", params).get("results", [])
