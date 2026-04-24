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
        
        # 2. FETCH THE GRAPH (Buffered by ~100m to catch the outer roads)
        # We fetch slightly wider than the suburb, then filter roads only in the polygon
        buffered_poly = polygon.buffer(0.001) 
        
        # Filter: ONLY quiet roads (No highways, no commercial main strips)
        cf = '["highway"~"residential|living_street|unclassified|tertiary"]'
        
        # Download map
        G = ox.graph_from_polygon(buffered_poly, network_type='walk', custom_filter=cf, simplify=True)
        
        # 3. CLEAN THE GRAPH (Fixes the "4 lines" and "Spiderwebs" bugs)
        # Force strict single-lines for physical streets, regardless of OSM tagging
        G_un = ox.utils_graph.get_undirected(G)
        
        # Strip out floating disconnected streets
        largest_cc = max(nx.connected_components(G_un), key=len)
        G_clean = G_un.subgraph(largest_cc).copy()

        # 4. BUILD THE MASTER ROUTING GRAPH (The 1-side vs 2-side logic)
        G_route = nx.MultiGraph()
        
        for u, v, key, data in G_clean.edges(keys=True, data=True):
            # Extract geometry
            if 'geometry' in data:
                geom = data['geometry']
            else:
                geom = LineString([(G_clean.nodes[u]['x'], G_clean.nodes[u]['y']), 
                                   (G_clean.nodes[v]['x'], G_clean.nodes[v]['y'])])
                data['geometry'] = geom

            # Check if this road is on the border (within ~40 meters of the boundary line)
            is_boundary = geom.distance(boundary_line) < 0.0004 
            
            # Add nodes
            G_route.add_node(u, x=G_clean.nodes[u]['x'], y=G_clean.nodes[u]['y'])
            G_route.add_node(v, x=G_clean.nodes[v]['x'], y=G_clean.nodes[v]['y'])
            
            # The Magic: 1 edge for boundaries, 2 edges for internal residential
            if is_boundary:
                G_route.add_edge(u, v, **data) # Walk once
            else:
                G_route.add_edge(u, v, **data) # Walk down
                G_route.add_edge(u, v, **data) # Walk back
                
        # 5. EULERIZE
        # Because boundary roads are 1 pass, we might have odd intersections. 
        # Eulerize perfectly mathematically connects them so you never get stuck.
        G_euler = nx.eulerize(G_route)
        circuit = list(nx.eulerian_circuit(G_euler))
        
        # 6. DRAW THE PATH WITH SMART VECTOR SHIFTING
        route_coords = []
        
        for u, v in circuit:
            # Get the physical road geometry
            edge_data = G_clean.get_edge_data(u, v)
            if not edge_data:
                edge_data = G_clean.get_edge_data(v, u)
            geom = edge_data[0]['geometry']
            
            coords = list(geom.coords)
            
            # Ensure the line coordinates flow in the direction we are actually walking (u to v)
            start_point = coords[0]
            node_u_coords = (G_clean.nodes[u]['x'], G_clean.nodes[u]['y'])
            
            # If the geometry was drawn backwards in OpenStreetMap, flip it
            dist_to_start = (start_point[0] - node_u_coords[0])**2 + (start_point[1] - node_u_coords[1])**2
            dist_to_end = (coords[-1][0] - node_u_coords[0])**2 + (coords[-1][1] - node_u_coords[1])**2
            if dist_to_end < dist_to_start:
                coords.reverse()

            # Dynamic Right-Hand Shift (creates the perfect "sidewalk" double lines)
            offset_coords = []
            for i in range(len(coords) - 1):
                x1, y1 = coords[i]
                x2, y2 = coords[i+1]
                
                # Calculate vector direction
                dx = x2 - x1
                dy = y2 - y1
                length = math.hypot(dx, dy)
                
                if length == 0:
                    continue
                    
                # Calculate the 90-degree Right normal vector
                nx_vec = dy / length
                ny_vec = -dx / length
                
                # Shift by ~3 meters (0.00003 degrees)
                offset = 0.00003
                
                new_x1 = x1 + (nx_vec * offset)
                new_y1 = y1 + (ny_vec * offset)
                
                if i == 0:
                    offset_coords.append({"lat": new_y1, "lng": new_x1})
                    
                new_x2 = x2 + (nx_vec * offset)
                new_y2 = y2 + (ny_vec * offset)
                offset_coords.append({"lat": new_y2, "lng": new_x2})
                
            route_coords.extend(offset_coords)

        return jsonify(route_coords)

    except Exception as e:
        print(traceback.format_exc())
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
