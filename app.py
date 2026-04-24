import osmnx as ox
import networkx as nx
from flask import Flask, request, jsonify, Response
from shapely.geometry import LineString, Point
import traceback
import math

app = Flask(__name__)

@app.route('/get_route', methods=['GET'])
def get_route():
    suburb = request.args.get('suburb', 'Edwardstown')
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

        # 4. BUILD ROUTING GRAPH
        G_route = nx.MultiGraph()
        for u, v, key, data in G_clean.edges(keys=True, data=True):
            if 'geometry' in data:
                geom = data['geometry']
            else:
                geom = LineString([(G_clean.nodes[u]['x'], G_clean.nodes[u]['y']), 
                                   (G_clean.nodes[v]['x'], G_clean.nodes[v]['y'])])
                data['geometry'] = geom

            is_boundary = geom.distance(boundary_line) < 0.0004 
            G_route.add_node(u, x=G_clean.nodes[u]['x'], y=G_clean.nodes[u]['y'])
            G_route.add_node(v, x=G_clean.nodes[v]['x'], y=G_clean.nodes[v]['y'])
            
            if is_boundary:
                G_route.add_edge(u, v, **data) 
            else:
                G_route.add_edge(u, v, **data) 
                G_route.add_edge(u, v, **data) 
                
        # 5. EULERIZE
        G_euler = nx.eulerize(G_route)
        circuit = list(nx.eulerian_circuit(G_euler))
        
        # 6. CONTINUOUS MITER SHIFT WITH ROUNDABOUT DAMPENER
        raw_continuous_coords = []
        for u, v in circuit:
            edge_data = G_clean.get_edge_data(u, v)
            if not edge_data:
                edge_data = G_clean.get_edge_data(v, u)
            geom = edge_data[0]['geometry']
            coords = list(geom.coords)
            
            start_point = coords[0]
            node_u_coords = (G_clean.nodes[u]['x'], G_clean.nodes[u]['y'])
            
            dist_to_start = (start_point[0] - node_u_coords[0])**2 + (start_point[1] - node_u_coords[1])**2
            dist_to_end = (coords[-1][0] - node_u_coords[0])**2 + (coords[-1][1] - node_u_coords[1])**2
            if dist_to_end < dist_to_start:
                coords.reverse()

            if not raw_continuous_coords:
                raw_continuous_coords.extend(coords)
            else:
                raw_continuous_coords.extend(coords[1:])

        route_coords = []
        base_offset = 0.00003  # ~3 meter shift
        num_points = len(raw_continuous_coords)

        for i in range(num_points):
            curr = raw_continuous_coords[i]
            
            if i == 0:
                nxt = raw_continuous_coords[i+1]
                dx, dy = nxt[0] - curr[0], nxt[1] - curr[1]
                L = math.hypot(dx, dy) or 1
                route_coords.append({"lat": curr[1] + (dy/L)*base_offset, "lng": curr[0] + (-dx/L)*base_offset})
                continue
            elif i == num_points - 1:
                prev = raw_continuous_coords[i-1]
                dx, dy = curr[0] - prev[0], curr[1] - prev[1]
                L = math.hypot(dx, dy) or 1
                route_coords.append({"lat": curr[1] + (dy/L)*base_offset, "lng": curr[0] + (-dx/L)*base_offset})
                continue

            prev = raw_continuous_coords[i-1]
            nxt = raw_continuous_coords[i+1]
            
            dx1, dy1 = curr[0] - prev[0], curr[1] - prev[1]
            dx2, dy2 = nxt[0] - curr[0], nxt[1] - curr[1]
            
            L1 = math.hypot(dx1, dy1) or 1
            L2 = math.hypot(dx2, dy2) or 1
            
            ux1, uy1 = dx1/L1, dy1/L1
            ux2, uy2 = dx2/L2, dy2/L2
            
            nx1, ny1 = uy1, -ux1
            nx2, ny2 = uy2, -ux2
            
            nx_avg = nx1 + nx2
            ny_avg = ny1 + ny2
            L_avg = math.hypot(nx_avg, ny_avg)
            
            # THE FIX: Roundabout & Sharp Turn Dampener
            # If the segment is very short (< ~15 meters), reduce the offset to prevent overlap
            min_len = min(L1, L2)
            current_offset = base_offset
            if min_len < 0.00015:
                current_offset = base_offset * (min_len / 0.00015)
            
            if L_avg < 0.1: # 180 U-turn safe fallback
                off_x, off_y = nx1 * current_offset, ny1 * current_offset
            else:
                nx_avg /= L_avg
                ny_avg /= L_avg
                dot = max(-1.0, min(1.0, ux1*ux2 + uy1*uy2))
                cos_half_theta = math.sqrt((1.0 + dot) / 2.0)
                
                # THE FIX: Cap the spike factor at 1.5x (stops corners from shooting out)
                factor = current_offset / max(cos_half_theta, 0.6)
                off_x, off_y = nx_avg * factor, ny_avg * factor
                
            route_coords.append({"lat": curr[1] + off_y, "lng": curr[0] + off_x})

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
