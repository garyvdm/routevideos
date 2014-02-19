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
parser.add_argument('file', action='store',
                    help='Route file.')
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
        n_points = int(round(gd['s12'] / 5))
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
            if dist > smallest_dist and dist >= 10:
                break
        return (smallest_dist, closest_point)

    pano_ids = set()
    if 'panos' not in data:
        data['panos'] = []
    if 'pano_links' not in data:
        data['pano_links'] = collections.OrderedDict()

    if data['panos']:
        def dup_check(pano):
            if pano['id'] in pano_ids:
                return False
            pano_ids.add(pano['id'])
            return True

        data['panos'] = [pano for pano in data['panos'] if dup_check(pano)]
        last_pano = data['panos'][-1]
        smallest_dist, last_point = get_closest_point(last_pano['lat'], last_pano['lng'], points_indexed[0])
    else:
        last_pano = None
        last_point = points_indexed[0]
    panos = data['panos']

    pano_links = data['pano_links']

    logging.info('Fetching pano data.')

    while True:
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
            next_point = points_indexed[last_point[2] + 1]
            yaw_to_next = geodesic.Inverse(last_point[0], last_point[1],
                                           next_point[0], next_point[1])['azi1'] % 360
            yaw_diff = lambda item: (abs(item['yaw'] - yaw_to_next)) % 360
            pano_link = min(last_pano['links'], key=yaw_diff)

            if yaw_diff(pano_link) > 15:
                logging.debug("Yaw too different: {} {} {}".format(yaw_diff(pano_link), pano_link['yaw'], yaw_to_next))
                last_pano = None
                pano_data = None
            else:
                #logging.debug('Get for {}'.format(pano_link['panoId']))
                pano_data = requests.get(
                    'http://cbks0.googleapis.com/cbk',
                    params={
                        'output': 'json',
                        'panoid': pano_link['panoId'],
                        'key': 'AIzaSyC74vPZz2tYpRuRWY7kZ8iaQ17Xam1-_-A',
                    }).json()

        if pano_data:
            location = pano_data['Location']
            pano_lat = float(location['lat'])
            pano_lng = float(location['lng'])
            if location['panoId'] not in pano_ids:
                smallest_dist, closest_point = get_closest_point(pano_lat, pano_lng, last_point)

                if smallest_dist > 3:
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
                    for link in pano_data['Links']:
                        if link['panoId'] in pano_ids:
                            key = '-'.join((link['panoId'], location['panoId']))
                            pano_links[key] = round((float(link['yawDeg']) + 180) % 360, 2)
                            has_link = True
                    last_pano = pano
                    logging.info("{description} ({lat},{lng}) {i}".format(**pano))
            else:
                last_point = points_indexed[last_point[2] + 1]
                last_pano = None

        if len(panos) >= 500:
            break

    last_yaw = 0
    for i, pano in enumerate(panos[:-1]):
        next_pano = panos[i + 1]
        prev_pano = panos[i - 1]

        if 'exclued' not in pano:
            link_key = '-'.join((pano['id'], next_pano['id']))
            yaw = pano_links.get(link_key, last_yaw)
            last_yaw = yaw
            path = 'pano_img/{}-{}.jpeg'.format(pano['id'], yaw)

            if not os.path.exists(path):
                logging.info('{} {}'.format(path, i))
                img = requests.get(
                    'http://maps.googleapis.com/maps/api/streetview',
                    params={
                        'size': '640x480',
                        'pano': pano['id'],
                        'fov': 110,
                        'heading': yaw,
                        'sensor': 'false',
                        'key': 'AIzaSyC74vPZz2tYpRuRWY7kZ8iaQ17Xam1-_-A',
                    },
                    stream=True,
                )
                img.raise_for_status()

                with open(path, 'wb') as f:
                    shutil.copyfileobj(img.raw, f)
                del img
            sln_path = 'bynum/{:08d}.jpeg'.format(pano['i'])
            if not os.path.exists(sln_path):
                #os.symlink('../{}'.format(path), sln_path)
                os.link(path, sln_path)


except:
    logging.exception('')
finally:
    pass
    logging.info('Saving file.')
    yaml_out = yaml.dump(data)
    with open(args.file, 'w') as f:
        f.write(yaml_out)
