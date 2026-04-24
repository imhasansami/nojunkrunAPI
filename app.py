import osmnx as ox
import networkx as nx
from flask import Flask, request, jsonify
from shapely.geometry import LineString, Point
import traceback
import math

app = Flask(__name__)

@app.route('/get_route', methods=['GET'])
def get_route():
    suburb = request.args.get('suburb', 'Edwardstown')
    query = f"{suburb}, South Australia, Australia"
    
    try:
        # 1. GET THE EXACT BOUNDARY
        gdf = ox.geocode_to_gdf(query)
        polygon = gdf.geometry.iloc[0]
        boundary_line = polygon.boundary
        
        # 2. FETCH THE GRAPH
        buffered_poly = polygon.buffer(0.001) 
        cf = '["highway"~"residential|living_street|unclassified|tertiary"]'
        G = ox.graph_from_polygon(buffered_poly, network_type='walk', custom_filter=cf, simplify=True)
        
        # 3. CLEAN THE GRAPH 
        G_un = ox.utils_graph.get_undirected(G)
        largest_cc = max(nx.connected_components(G_un), key=len)
        G_clean = G_un.subgraph(largest_cc).copy()

        # 4. BUILD THE MASTER ROUTING GRAPH
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
        
        # 6. CONTINUOUS MITER SHIFT (Fixes the intersection rectangles)
        raw_continuous_coords = []
        
        # Step A: Flatten the entire walk into one continuous list of coordinates
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
                # Skip the first point to prevent duplicates exactly at the intersection joint
                raw_continuous_coords.extend(coords[1:])

        # Step B: Apply a continuous geometric right-hand offset
        route_coords = []
        offset_dist = 0.00003  # ~3 meter sidewalk shift
        num_points = len(raw_continuous_coords)

        for i in range(num_points):
            curr = raw_continuous_coords[i]
            
            if i == 0:
                nxt = raw_continuous_coords[i+1]
                dx, dy = nxt[0] - curr[0], nxt[1] - curr[1]
            elif i == num_points - 1:
                prev = raw_continuous_coords[i-1]
                dx, dy = curr[0] - prev[0], curr[1] - prev[1]
            else:
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
                
                if L_avg < 0.1: # 180 degree U-Turn detected, prevent math explosion
                    ox, oy = nx1 * offset_dist, ny1 * offset_dist
                else:
                    nx_avg /= L_avg
                    ny_avg /= L_avg
                    dot = max(-1.0, min(1.0, ux1*ux2 + uy1*uy2))
                    cos_half_theta = math.sqrt((1.0 + dot) / 2.0)
                    # Cap the spike factor so super sharp corners don't fly off the screen
                    factor = offset_dist / max(cos_half_theta, 0.2)
                    ox, oy = nx_avg * factor, ny_avg * factor
                    
                route_coords.append({"lat": curr[1] + oy, "lng": curr[0] + ox})
                continue
                
            # Normal calculation for the very first and last points of the entire route
            L = math.hypot(dx, dy) or 1
            nx, ny = dy/L, -dx/L
            route_coords.append({"lat": curr[1] + ny * offset_dist, "lng": curr[0] + nx * offset_dist})

        return jsonify(route_coords)

    except Exception as e:
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
