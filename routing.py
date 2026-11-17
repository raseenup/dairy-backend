"""Local distance/cost route planning. No addresses go to a geocoding service.

Nearest-neighbour plus 2-opt reduces estimated travel distance for a required
set of stops. It is not a traffic/road engine or a guaranteed global optimum.
"""
import math
from datetime import date
from functools import lru_cache

from fastapi import Request
from pydantic import Field

from workforce import StrictModel, core, staff, may_manage

class Coordinates(StrictModel):
    latitude: float=Field(ge=-90,le=90,allow_inf_nan=False)
    longitude: float=Field(ge=-180,le=180,allow_inf_nan=False)

class RouteSettings(Coordinates):
    cost_per_km: float=Field(ge=0,le=10000,allow_inf_nan=False)
    cost_per_stop: float=Field(ge=0,le=10000,allow_inf_nan=False)
    unit_costs: dict[str,float]=Field(default_factory=dict,max_length=500)


def km(a,b):
    lat1,lon1,lat2,lon2=map(math.radians,(a[0],a[1],b[0],b[1]))
    delta=math.sin((lat2-lat1)/2)**2+math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 6371*2*math.asin(min(1,math.sqrt(delta)))


@lru_cache(maxsize=256)
def sequence(origin,points):
    remaining=list(range(len(points)));route=[];current=origin
    while remaining:
        nxt=min(remaining,key=lambda i:km(current,points[i]));remaining.remove(nxt);route.append(nxt);current=points[nxt]
    def distance(order):
        path=[origin]+[points[i] for i in order]
        return sum(km(path[i],path[i+1]) for i in range(len(path)-1))
    # Bound work so a fleet refresh cannot occupy a request worker indefinitely.
    best=distance(route)
    for _ in range(2):
        changed=False
        for left in range(len(route)-1):
            for right in range(left+2,len(route)+1):
                candidate=route[:left]+route[left:right][::-1]+route[right:];value=distance(candidate)
                if value+0.00001<best:route,best,changed=candidate,value,True
        if not changed:break
    return tuple(route),round(best,2)


def route_plan(state,driver_id,admin):
    c=core();settings=state.get('route_settings',{})
    active=[o for o in state['orders'].values() if o['status'] not in {'Delivered','Failed','Cancelled','Awaiting Payment'}
            and ((driver_id=='unassigned' and not o.get('assigned_driver_id')) or o.get('assigned_driver_id')==driver_id)]
    origin=state.get('driver_route_origins',{}).get(driver_id) or settings
    missing=[o for o in active if o.get('delivery_latitude') is None or o.get('delivery_longitude') is None]
    known=[o for o in active if o.get('delivery_latitude') is not None and o.get('delivery_longitude') is not None]
    origin_ok=origin.get('latitude') is not None and origin.get('longitude') is not None
    ready=origin_ok and bool(known)
    route=[];distance=0
    if ready:
        # Deliveries are planned by delivery date. Stops for later dates do not
        # displace today's promises just because their coordinates are closer.
        current=(origin['latitude'],origin['longitude'])
        for day in sorted({o['delivery_date'] for o in known}):
            batch=[o for o in known if o['delivery_date']==day]
            for start in range(0,len(batch),40):
                part=batch[start:start+40];points=tuple((o['delivery_latitude'],o['delivery_longitude']) for o in part)
                indices,part_distance=sequence(current,points);distance+=part_distance
                for i in indices:route.append(part[i])
                if indices:current=points[indices[-1]]
    stops=[dict(order_id=o['id'],order_number=o['order_number'],address=o['delivery_address'],delivery_date=o['delivery_date'],
        latitude=o['delivery_latitude'],longitude=o['delivery_longitude'],customer_name=o['customer_name'],position=i+1) for i,o in enumerate(route)]
    directions=[]
    if stops:
        previous=(origin['latitude'],origin['longitude'])
        for index in range(0,len(stops),9):
            part=stops[index:index+9]
            waypoint='|'.join(str(s['latitude'])+','+str(s['longitude']) for s in part[:-1])
            end=part[-1];url='https://www.google.com/maps/dir/?api=1&origin=%s,%s&destination=%s,%s&travelmode=driving'%(previous[0],previous[1],end['latitude'],end['longitude'])
            if waypoint:url+='&waypoints='+waypoint.replace('|','%7C')
            directions.append(url);previous=(end['latitude'],end['longitude'])
    result=dict(driver_id=driver_id,status='ready' if ready and not missing else 'partial' if ready else 'needs_locations' if active else 'empty',
        origin={k:origin[k] for k in ('latitude','longitude') if k in origin},stops=stops,
        missing_locations=[dict(order_id=o['id'],address=o['delivery_address']) for o in missing],
        origin_required=bool(active) and not origin_ok,estimated_km=round(distance,2),directions_urls=directions,
        method='Date-prioritized nearest-neighbour and 2-opt; straight-line distance. Check road directions before driving.')
    if admin:
        costs=settings.get('unit_costs_paise',{});complete=bool(route) and not missing and all(l['product_id'] in costs for o in route for l in o['items'])
        merchandise=sum(costs.get(l['product_id'],0)*l['quantity'] for o in route for l in o['items'])
        operating=round(distance*settings.get('cost_per_km_paise',0))+len(route)*settings.get('cost_per_stop_paise',0)
        revenue=sum(o['subtotal_paise'] for o in route)
        result.update(estimated_delivery_cost=operating/100 if settings else None,
            estimated_profit=(revenue-merchandise-operating)/100 if complete and settings else None,
            cost_data_complete=complete and bool(settings),profit_note='Estimated contribution before unconfigured overheads; all required stops are retained. Enter unit costs to calculate profit.')
    return result


def install_routing(app):
    c=core()
    @app.patch('/admin/route-settings',tags=['Delivery route planning'])
    def settings(request:Request,body:RouteSettings):
        with c.transaction() as state:
            user=staff(state,request)
            for ident,value in body.unit_costs.items():
                c.require(ident in state['products'],'Unknown product in cost settings.',422);c.money(value)
            item=dict(latitude=body.latitude,longitude=body.longitude,cost_per_km_paise=c.money(body.cost_per_km),
                cost_per_stop_paise=c.money(body.cost_per_stop),unit_costs_paise={k:c.money(v) for k,v in body.unit_costs.items()})
            state['route_settings']=item;c.audit(state,user['email'],'ROUTE_SETTINGS_UPDATED');return item

    @app.patch('/admin/deliveries/{order_id}/location',tags=['Delivery route planning'])
    def order_location(order_id:str,request:Request,body:Coordinates):
        with c.transaction() as state:
            user=staff(state,request);order=state['orders'].get(order_id);c.require(order,'Order not found.',404)
            c.require(order['status'] not in {'Delivered','Cancelled','Failed'},'Closed order location cannot be changed.',409)
            order.update(delivery_latitude=body.latitude,delivery_longitude=body.longitude)
            c.audit(state,user['email'],'DELIVERY_LOCATION_UPDATED',order_id);return {'message':'Delivery location saved. Routes will update automatically.'}

    @app.patch('/users/me/addresses/{address_id}/location',tags=['Delivery route planning'])
    def address_location(address_id:str,request:Request,body:Coordinates):
        with c.transaction() as state:
            user,_=c.actor(state,request,'customer');address=state.get('addresses',{}).get(address_id)
            c.require(address and address['owner']==user['email'],'Saved address not found.',404)
            address.update(latitude=body.latitude,longitude=body.longitude)
            c.audit(state,user['email'],'SAVED_ADDRESS_LOCATION_UPDATED',address_id);return address

    @app.patch('/driver/route-origin',tags=['Delivery route planning'])
    def driver_origin(request:Request,body:Coordinates):
        with c.transaction() as state:
            user,_=c.actor(state,request,'driver');state.setdefault('driver_route_origins',{})[user['id']]=dict(body.model_dump(),updated_at=c.now())
            c.audit(state,user['email'],'DRIVER_ROUTE_ORIGIN_UPDATED');return {'message':'Route start saved.'}

    @app.get('/driver/route',tags=['Delivery route planning'])
    def driver_route(request:Request):
        with c.read_transaction() as state:
            user,_=c.actor(state,request,'driver');return route_plan(state,user['id'],False)

    @app.get('/admin/routes/{driver_id}',tags=['Delivery route planning'])
    def admin_route(driver_id:str,request:Request):
        with c.read_transaction() as state:
            staff(state,request);return route_plan(state,driver_id,True)
