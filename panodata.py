#!/usr/bin/env python3

import argparse
import requests
import collections
import itertools
import logging
import shutil
import os
import os.path
import json

import yaml

import gpolyline
import geographiclib.geodesic

geodesic = geographiclib.geodesic.Geodesic.WGS84

parser = argparse.ArgumentParser()
parser.add_argument('file', action='store', help='Route file.')
parser.add_argument('--web-file', '-w', action='store', nargs='?', help='JSON web file')
args = parser.parse_args()

logging.basicConfig(level=logging.DEBUG)
logging.getLogger('requests').level = logging.ERROR

latlng_urlstr = lambda latlng: "{},{}".format(*latlng)


def dict_representer(dumper, data):
    return dumper.represent_mapping(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, data.items())


def dict_constructor(loader, node):
    return collections.OrderedDict(loader.construct_pairs(node))

yaml.add_representer(collections.OrderedDict, dict_representer)
yaml.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, dict_constructor)

logging.info('Loading file.')
with open(args.file, 'r') as f:
    data = yaml.load(f)

try:
    if 'route_response' not in data:
        logging.info('Fetching route.')
        waypoints = '|'.join(('via:{}'.format(latlng_urlstr(wp)) for wp in data['route_request']['waypoints']))
        data['route_response'] = requests.get(
            'https://maps.googleapis.com/maps/api/directions/json',
            params={
                'origin': latlng_urlstr(data['route_request']['origin']),
                'destination': latlng_urlstr(data['route_request']['origin']),
                'waypoints': waypoints,
                'sensor': 'false',
                'key': 'AIzaSyC74vPZz2tYpRuRWY7kZ8iaQ17Xam1-_-A',
            }).json()

    logging.info('Calculating pano retrival points.')
    steps = list(itertools.chain(*(leg['steps'] for leg in data['route_response']['routes'][0]['legs'])))
    step_points = (gpolyline.decode(step['polyline']['points']) for step in steps)
    points = list(itertools.chain(*(points if i == 0 else points[1:] for i, points in enumerate(step_points))))

    points_more = [points[0]]
    prev_point = points[0]

    for point in points[1:]:
        gd = geodesic.Inverse(prev_point[0], prev_point[1], point[0], point[1])
        line = geodesic.Line(gd['lat1'], gd['lon1'], gd['azi1'])
        n_points = int(round(gd['s12'] / 1))
        for i in range(1, n_points):
            more_point = line.Position(gd['s12'] / n_points * i)
            points_more.append((round(more_point['lat2'], 6), round(more_point['lon2'], 6)))
        points_more.append(point)
        prev_point = point

    logging.debug((len(points), len(points_more)))
    points_indexed = [(lat, lng, i) for i, (lat, lng) in enumerate(points_more)]

    def get_closest_point(lat, lng, last_point):
        smallest_dist = None
        closest_point = None
        for point in points_indexed[last_point[2]:]:
            dist = geodesic.Inverse(point[0], point[1], lat, lng)['s12']
            if smallest_dist is None or dist < smallest_dist:
                smallest_dist = dist
                closest_point = point
            if dist > smallest_dist and dist >= 10 and smallest_dist < 10:
                break
        return (smallest_dist, closest_point)

    pano_ids = set()
    if 'panos' not in data:
        data['panos'] = []

    if data['panos']:
        def dup_check(pano):
            if pano['id'] in pano_ids:
                return False
            pano_ids.add(pano['id'])
            return True

        data['panos'] = [pano for pano in data['panos'] if dup_check(pano)]
        last_pano = data['panos'][-1]
        last_point = points_indexed[last_pano['i']]
    else:
        last_pano = None
        last_point = points_indexed[0]
    panos = data['panos']
    prefered_pano_chain = data.get('prefered_pano_chain', {})
    exculded_panos = set(data.get('exculded_panos', []))

    logging.info('Fetching pano data.')

    try:
        while True:
            #if len(panos) >= 500:
            #    break
            if last_pano is None:
                for point in points_indexed[last_point[2]:]:
                    logging.debug('Get for ({},{}) {}'.format(*point))
                    pano_data = requests.get(
                        'http://cbks0.googleapis.com/cbk',
                        params={
                            'output': 'json',
                            'radius': 3,
                            'll': latlng_urlstr(point),
                            'key': 'AIzaSyC74vPZz2tYpRuRWY7kZ8iaQ17Xam1-_-A',
                        }).json()
                    if pano_data:
                        break
                else:
                    break
            else:
                if last_pano['id'] in prefered_pano_chain:
                    link_pano_id = prefered_pano_chain[last_pano['id']]
                else:
                    next_point = points_indexed[last_point[2] + 1]
                    yaw_to_next = geodesic.Inverse(last_point[0], last_point[1],
                                                   next_point[0], next_point[1])['azi1'] % 360
                    yaw_diff = lambda item: (abs(item['yaw'] - yaw_to_next)) % 360
                    pano_link = min(last_pano['links'], key=yaw_diff)
        
                    if yaw_diff(pano_link) > 20:
                        logging.debug("Yaw too different: {} {} {}".format(yaw_diff(pano_link), pano_link['yaw'], yaw_to_next))
                        link_pano_id = None
                    else:
                        link_pano_id = pano_link['panoId']
        
                if link_pano_id:
                    #logging.debug('Get for {}'.format(link_pano_id))
                    pano_data = requests.get(
                        'http://cbks0.googleapis.com/cbk',
                        params={
                            'output': 'json',
                            'panoid': link_pano_id,
                            'key': 'AIzaSyC74vPZz2tYpRuRWY7kZ8iaQ17Xam1-_-A',
                        }).json()
                else:
                    last_pano = None
                    pano_data = None
    
            if pano_data:
                location = pano_data['Location']
                pano_lat = float(location['lat'])
                pano_lng = float(location['lng'])
                if location['panoId'] not in pano_ids:
                    smallest_dist, closest_point = get_closest_point(pano_lat, pano_lng, last_point)
    
                    if smallest_dist > 10:
                        logging.debug("Distance {} to nearest point too great for pano: {}"
                                      .format(smallest_dist, location['panoId']))
                        last_pano = None
                    else:
                        last_point = closest_point
    
                        links = [collections.OrderedDict(panoId=link['panoId'], yaw=float(link['yawDeg']))
                                 for link in pano_data['Links']]
                        pano = collections.OrderedDict(
                            id=location['panoId'], lat=pano_lat, lng=pano_lng,
                            description=location['description'], links=links, i=last_point[2], )
                        if 'elevation_wgs84_m' in location:
                            pano['elv'] = float(location['elevation_wgs84_m'])
                        panos.append(pano)
                        pano_ids.add(location['panoId'])
                        last_pano = pano
                        logging.info("{description} ({lat},{lng}) {i}".format(**pano))
                else:
                    last_point = points_indexed[last_point[2] + 1]
                    last_pano = None
    except:
        logging.exception('')

    
    filtered_panos = [p for p in panos if p['id'] not in exculded_panos]

    logging.info("Calculating yaws")
    for pano, next_pano in zip(filtered_panos[:-1], filtered_panos[1:]):
        pano['yaw'] = round(geodesic.Inverse(pano['lat'], pano['lng'], next_pano['lat'], next_pano['lng'])['azi1'] % 360, 4)
    next_pano['yaw'] = round(geodesic.Inverse(pano['lat'], pano['lng'], next_pano['lat'], next_pano['lng'])['azi2'] % 360, 4)
    
    yaw_smooth_range = 4
    smooth_matrix = [1, 2, 3, 4, 5, 4, 3, 2, 1]
    matrix_sum = sum(smooth_matrix)
    product = lambda item: item[0] * item[1]
    filtered_panos_len = len(filtered_panos)
    for i in range(filtered_panos_len):
        smooth_values = [filtered_panos[min(max(j, 0), filtered_panos_len - 1)]['yaw']
                         for j in range(i - yaw_smooth_range, i + yaw_smooth_range + 1)]
        filtered_panos[i]['smooth_yaw'] = round(sum(map(product, zip(smooth_values, smooth_matrix))) / matrix_sum, 2)

    try:
        logging.info("Downloading frames")
        for i, pano in enumerate(filtered_panos):
            path = 'pano_img/{}-{}.jpeg'.format(pano['id'], pano['smooth_yaw'])
    
            if not os.path.exists(path):
                logging.info('{} {} {}'.format(path, i, pano['i']))
                img = requests.get(
                    'http://maps.googleapis.com/maps/api/streetview',
                    params={
                        'size': '640x480',
                        'pano': pano['id'],
                        'fov': 110,
                        'heading': pano['smooth_yaw'],
                        'sensor': 'false',
                        'key': 'AIzaSyC74vPZz2tYpRuRWY7kZ8iaQ17Xam1-_-A',
                    },
                    stream=True,
                )
                img.raise_for_status()
    
                with open(path, 'wb') as f:
                    shutil.copyfileobj(img.raw, f)
                del img
            os.symlink("../" + path, 'bynum/{:05d}.jpeg'.format(i))
            os.link(path, 'byid/{:05d}-{}-{:06d}.jpeg'.format(pano['i'], pano['id'], i))
    except:
        logging.exception('')

except:
    logging.exception('')
finally:
    pass
    logging.info('Saving file.')
    yaml_out = yaml.dump(data)
    with open(args.file, 'w') as f:
        f.write(yaml_out)

    filtered_panos = [pano for pano in panos if pano['id'] not in exculded_panos]
    web_data = collections.OrderedDict(
        pano_points=[(pano['lat'], pano['lng'], '{} - {}'.format(pano['i'], i)) for i, pano in list(enumerate(filtered_panos))[4800:4900]]
    )
    if args.web_file:
        with open(args.web_file, 'w') as f:
            json.dump(web_data, f)
