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
    start_lat = request.args.get('lat')
    start_lng = request.args.get('lng')
    
    query = f"{suburb}, South Australia, Australia"
    
    try:
        # 1. BOUNDARY
        gdf = ox.geocode_to_gdf(query)
        polygon = gdf.geometry.iloc[0]
        boundary_line = polygon.boundary
        
        # 2. FETCH GRAPH
        buffered_poly = polygon.buffer(0.001) 
        cf = '["highway"~"residential|living_street|unclassified|tertiary"]'
        G = ox.graph_from_polygon(buffered_poly, network_type='walk', custom_filter=cf, simplify=True)
        
        # 3. CLEAN GRAPH
        G_un = ox.utils_graph.get_undirected(G)
        largest_cc = max(nx.connected_components(G_un), key=len)
        G_clean = G_un.subgraph(largest_cc).copy()

        # 4. MASTER ROUTING GRAPH (Double-sided Logic)
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
                
        # 5. EULERIZE & DYNAMIC START
        G_euler = nx.eulerize(G_route)
        
        if start_lat and start_lng:
            source_node = ox.distance.nearest_nodes(G_clean, float(start_lng), float(start_lat))
            circuit = list(nx.eulerian_circuit(G_euler, source=source_node))
        else:
            circuit = list(nx.eulerian_circuit(G_euler))
        
        # 6. FLATTEN TO TRUE CONTINUOUS ARRAY
        raw_coords = []
        for u, v in circuit:
            edge_data = G_clean.get_edge_data(u, v)
            if not edge_data:
                edge_data = G_clean.get_edge_data(v, u)
            geom = edge_data[0]['geometry']
            
            coords = list(geom.coords)
            
            # Ensure proper flow direction
            node_u_coords = (G_clean.nodes[u]['x'], G_clean.nodes[u]['y'])
            start_point = coords[0]
            dist_to_start = (start_point[0] - node_u_coords[0])**2 + (start_point[1] - node_u_coords[1])**2
            
            if dist_to_start > 1e-10:
                coords.reverse()

            if not raw_coords:
                raw_coords.extend(coords)
            else:
                # Drop the duplicate intersection node to fuse the lines perfectly
                raw_coords.extend(coords[1:])
                
        # 7. CONTINUOUS MITER SPLINE & U-TURN FIX
        route_coords = []
        OFFSET = 0.000025  # ~2.5 meter right shift
        n = len(raw_coords)
        
        for i in range(n):
            if i == 0:
                p1, p2 = raw_coords[0], raw_coords[1]
                dx, dy = p2[0] - p1[0], p2[1] - p1[1]
                L = math.hypot(dx, dy) or 1
                route_coords.append({"lat": p1[1] + (-dx/L)*OFFSET, "lng": p1[0] + (dy/L)*OFFSET})
            elif i == n - 1:
                p1, p2 = raw_coords[n-2], raw_coords[n-1]
                dx, dy = p2[0] - p1[0], p2[1] - p1[1]
                L = math.hypot(dx, dy) or 1
                route_coords.append({"lat": p2[1] + (-dx/L)*OFFSET, "lng": p2[0] + (dy/L)*OFFSET})
            else:
                prev = raw_coords[i-1]
                curr = raw_coords[i]
                nxt = raw_coords[i+1]
                
                dx1, dy1 = curr[0] - prev[0], curr[1] - prev[1]
                dx2, dy2 = nxt[0] - curr[0], nxt[1] - curr[1]
                
                L1, L2 = math.hypot(dx1, dy1), math.hypot(dx2, dy2)
                
                if L1 < 1e-8 or L2 < 1e-8:
                    continue
                    
                # Right-hand normals
                nx1, ny1 = dy1/L1, -dx1/L1
                nx2, ny2 = dy2/L2, -dx2/L2
                
                dot = (dx1/L1)*(dx2/L2) + (dy1/L1)*(dy2/L2)
                
                # U-TURN DETECTOR: If turning sharper than 143 degrees
                if dot < -0.8:
                    # Do not miter. Cross the street like a crosswalk.
                    route_coords.append({"lat": curr[1] + ny1*OFFSET, "lng": curr[0] + nx1*OFFSET})
                    route_coords.append({"lat": curr[1] + ny2*OFFSET, "lng": curr[0] + nx2*OFFSET})
                else:
                    # Normal smooth corner
                    nx_avg, ny_avg = nx1 + nx2, ny1 + ny2
                    L_avg = math.hypot(nx_avg, ny_avg)
                    
                    if L_avg > 1e-8:
                        nx_avg /= L_avg
                        ny_avg /= L_avg
                        
                        clamped_dot = max(-0.999, dot)
                        cos_half_theta = math.sqrt((1.0 + clamped_dot) / 2.0)
                        factor = OFFSET / max(cos_half_theta, 0.3) # Caps the spike length
                        
                        route_coords.append({"lat": curr[1] + ny_avg*factor, "lng": curr[0] + nx_avg*factor})
                    else:
                        route_coords.append({"lat": curr[1] + ny1*OFFSET, "lng": curr[0] + nx1*OFFSET})

        # 8. EXPORT LOGIC
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
