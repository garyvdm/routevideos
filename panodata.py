#!/usr/bin/env python3

import argparse
import collections
import functools
import itertools
import json
import logging
import os
import os.path
import requests
import shutil

import geographiclib.geodesic
import gpolyline
import yaml

geodesic = geographiclib.geodesic.Geodesic.WGS84

def dict_representer(dumper, data):
    return dumper.represent_mapping(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, data.items())


def dict_constructor(loader, node):
    return collections.OrderedDict(loader.construct_pairs(node))

yaml.add_representer(collections.OrderedDict, dict_representer)
yaml.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, dict_constructor)



try:
    
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', action='store', help='Route directory. This should contain atleast `source.yaml`.')
    parser.add_argument('--debug', action='store_true', help='Output DEBUG messages.')
    
    args = parser.parse_args()
    dir_join = functools.partial(os.path.join, args.directory)
    
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)
    logging.getLogger('requests').level = logging.ERROR
    
    latlng_urlstr = lambda latlng: "{},{}".format(*latlng)
    
    logging.info('Loading source.yaml')
    with open(dir_join('source.yaml'), 'r') as f:
        source = yaml.load(f)


    if os.path.exists(dir_join('route.json')):
        logging.info('loading route.json')
        with open(dir_join('route.json'), 'r') as f:
            route = json.load(f)
    else:
        logging.info('Fetching route')
        waypoints = '|'.join(('via:{}'.format(latlng_urlstr(wp)) for wp in source['route_request']['waypoints']))
        route = requests.get(
            'https://maps.googleapis.com/maps/api/directions/json',
            params={
                'origin': latlng_urlstr(source['route_request']['origin']),
                'destination': latlng_urlstr(source['route_request']['destination']),
                'waypoints': waypoints,
                'sensor': 'false',
                'key': 'AIzaSyC74vPZz2tYpRuRWY7kZ8iaQ17Xam1-_-A',
            }).json()
        logging.info('Saving route.json')
        with open(dir_join('route.json'), 'w') as f:
            json.dump(route, f, indent=2)

    logging.info('Calculating additional route points.')
    steps = list(itertools.chain(*(leg['steps'] for leg in route['routes'][0]['legs'])))
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

    if os.path.exists(dir_join('panos.json')):
        logging.info('loading panos.json')
        with open(dir_join('panos.json'), 'r') as f:
            panos = json.load(f)
    else:
        panos = []
    
    try:
        pano_ids = set([pano['id'] for pano in panos])
        prefered_pano_chain = source.get('prefered_pano_chain', {})
        exculded_panos = set(source.get('exculded_panos', []))
        
        try:
            logging.info('Fetching pano data.')
            if panos:
                last_pano = panos[-1]
                last_point = points_indexed[last_pano['i']]
            else:
                last_pano = None
                last_point = points_indexed[0]
    
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
        
                            links = [dict(panoId=link['panoId'], yaw=float(link['yawDeg']))
                                     for link in pano_data['Links']]
                            pano = dict(
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
        except KeyboardInterrupt:
            logging.error('KeyboardInterrupt')
        
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
        def deg_wrap_to_closest(deg, to_deg):
            up = deg + 360
            down = deg - 360
            return min(deg, up, down, key=lambda x: abs(to_deg - x))
        
        for i in range(filtered_panos_len):
            this_yaw = filtered_panos[i]['yaw']
            smooth_values = [deg_wrap_to_closest(filtered_panos[min(max(j, 0), filtered_panos_len - 1)]['yaw'], this_yaw)
                             for j in range(i - yaw_smooth_range, i + yaw_smooth_range + 1)]
            filtered_panos[i]['smooth_yaw'] = round((sum(map(product, zip(smooth_values, smooth_matrix))) / matrix_sum) % 360, 2) 

        for pano in filtered_panos:
            pano['yaw'] = round(pano['yaw'] % 360, 2)

    finally:
        logging.info('Saving panos.json')
        
        with open(dir_join('panos.json'), 'w') as f:
            f.write('[\n')
            for i, pano in enumerate(panos):
                f.write('  ')
                json.dump(pano, f, sort_keys=True)
                if i == len(panos) - 1:
                    f.write('\n')
                else:
                    f.write(',\n')
            f.write(']\n')
        
        point_debug = dict(
            pano_points=[(pano['lat'], pano['lng'], '{} - {}'.format(pano['i'], i)) for i, pano in list(enumerate(filtered_panos))[4800:4900]]
        )
        logging.info('Saving point_debug.json')
        with open(dir_join('point_debug.json'), 'w') as f:
            json.dump(point_debug, f)
    
    logging.info("Downloading frames")
    if os.path.exists(dir_join('bynum')):
        shutil.rmtree(dir_join('bynum'))
    os.makedirs(dir_join('bynum'))
    if os.path.exists(dir_join('byid')):
        shutil.rmtree(dir_join('byid'))
    os.makedirs(dir_join('byid'))
    
    
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
        os.link(path, dir_join('bynum/{:05d}.jpeg'.format(i)))
        os.link(path, dir_join('byid/{:05d}-{}-{:06d}.jpeg'.format(pano['i'], pano['id'], i)))


except KeyboardInterrupt:
    logging.error('KeyboardInterrupt')
except:
    logging.exception('')



