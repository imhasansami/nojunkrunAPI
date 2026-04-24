import osmnx as ox
import networkx as nx
from flask import Flask, request, jsonify, Response
from shapely.geometry import LineString
import traceback
import math

app = Flask(__name__)

@app.route('/get_route', methods=['GET'])
def get_route():
    suburb = request.args.get('suburb', 'Edwardstown')
    
    # NEW: Accept start coordinates from Android
    start_lat = request.args.get('lat')
    start_lng = request.args.get('lng')
    
    query = f"{suburb}, South Australia, Australia"
    
    try:
        # 1. BOUNDARY
        gdf = ox.geocode_to_gdf(query)
        polygon = gdf.geometry.iloc[0]
        boundary_line = polygon.boundary
        
        # 2. FETCH GRAPH (Commercial areas removed)
        buffered_poly = polygon.buffer(0.001) 
        # Removed 'secondary' and 'primary' to strictly block commercial main strips
        cf = '["highway"~"residential|living_street|unclassified|tertiary"]'
        G = ox.graph_from_polygon(buffered_poly, network_type='walk', custom_filter=cf, simplify=True)
        
        # 3. CLEAN GRAPH
        G_un = ox.utils_graph.get_undirected(G)
        largest_cc = max(nx.connected_components(G_un), key=len)
        G_clean = G_un.subgraph(largest_cc).copy()

        # 4. MASTER ROUTING GRAPH
        G_route = nx.MultiGraph()
        
        for u, v, key, data in G_clean.edges(keys=True, data=True):
            if 'geometry' in data:
                geom = data['geometry']
            else:
                geom = LineString([(G_clean.nodes[u]['x'], G_clean.nodes[u]['y']), 
                                   (G_clean.nodes[v]['x'], G_clean.nodes[v]['y'])])
                data['geometry'] = geom

            is_boundary = geom.distance(boundary_line) < 0.0006 
            
            G_route.add_node(u, x=G_clean.nodes[u]['x'], y=G_clean.nodes[u]['y'])
            G_route.add_node(v, x=G_clean.nodes[v]['x'], y=G_clean.nodes[v]['y'])
            
            if is_boundary:
                G_route.add_edge(u, v, **data) 
            else:
                G_route.add_edge(u, v, **data) 
                G_route.add_edge(u, v, **data) 
                
        # 5. EULERIZE & DYNAMIC START POINT
        G_euler = nx.eulerize(G_route)
        
        # Determine where the route should begin
        if start_lat and start_lng:
            # Find the node closest to the user's GPS coordinates
            source_node = ox.distance.nearest_nodes(G_clean, float(start_lng), float(start_lat))
            circuit = list(nx.eulerian_circuit(G_euler, source=source_node))
        else:
            # Default behavior if no GPS is provided
            circuit = list(nx.eulerian_circuit(G_euler))
        
        # 6. CONTINUOUS SEQUENTIAL SHIFT (Stable double-sided sidewalks)
        route_coords = []
        OFFSET = 0.000025  
        
        for u, v in circuit:
            edge_data = G_clean.get_edge_data(u, v)
            if not edge_data:
                edge_data = G_clean.get_edge_data(v, u)
            geom = edge_data[0]['geometry']
            
            coords = list(geom.coords)
            
            node_u_coords = (G_clean.nodes[u]['x'], G_clean.nodes[u]['y'])
            start_point = coords[0]
            dist_to_start = (start_point[0] - node_u_coords[0])**2 + (start_point[1] - node_u_coords[1])**2
            if dist_to_start > 1e-10:
                coords.reverse()

            for i in range(len(coords) - 1):
                x1, y1 = coords[i]
                x2, y2 = coords[i+1]
                
                dx = x2 - x1
                dy = y2 - y1
                length = math.hypot(dx, dy)
                
                if length == 0:
                    continue
                    
                nx_vec = dy / length
                ny_vec = -dx / length
                
                off_x = nx_vec * OFFSET
                off_y = ny_vec * OFFSET
                
                if i == 0:
                    route_coords.append({"lat": y1 + off_y, "lng": x1 + off_x})
                route_coords.append({"lat": y2 + off_y, "lng": x2 + off_x})

        # 7. EXPORT LOGIC
        format_type = request.args.get('format', 'json')
        if format_type == 'kml':
            kml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
            <kml xmlns="http://www.opengis.net/kml/2.2">
              <Document><name>{suburb} Route</name><Placemark><LineString><coordinates>"""
            for pt in route_coords:
                kml_content += f"{pt['lng']},{pt['lat']},0 "
            kml_content += """</coordinates></LineString></Placemark></Document></kml>"""
            safe_filename = suburb.replace(' ', '_') + "_route.kml"
            return Response(kml_content, mimetype='application/vnd.google-earth.kml+xml', headers={"Content-disposition": f"attachment; filename={safe_filename}"})
        else:
            return jsonify(route_coords)

    except Exception as e:
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
