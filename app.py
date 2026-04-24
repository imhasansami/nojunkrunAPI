import osmnx as ox
import networkx as nx
from flask import Flask, request, jsonify
from shapely.geometry import LineString
import traceback

app = Flask(__name__)

@app.route('/get_route', methods=['GET'])
def get_route():
    suburb = request.args.get('suburb', 'Edwardstown')
    query = f"{suburb}, South Australia, Australia"
    
    try:
        # 1. BOUNDARY FIX
        gdf = ox.geocode_to_gdf(query)
        gdf_proj = ox.project_gdf(gdf)
        buffered_geom = gdf_proj.geometry.buffer(50) 
        gdf_buffered = ox.project_gdf(gdf_proj.set_geometry(buffered_geom), to_latlong=True)
        polygon = gdf_buffered.geometry.iloc[0]
        
        # 2. FILTER FIX (Only residential)
        cf = '["highway"~"residential|living_street"]'
        G = ox.graph_from_polygon(polygon, network_type='walk', custom_filter=cf, simplify=True)
        
        # 3. THE CRASH FIX (Strip directions to find connected chunks)
        G_un = G.to_undirected()
        largest_cc = max(nx.connected_components(G_un), key=len)
        
        # Keep only the connected roads from the original graph
        G = G.subgraph(largest_cc).copy()
        
        # 4. THE MATH FIX ("Both Sides" Guarantee)
        # Convert to undirected, then to directed. This mathematically forces 
        # exactly one forward and one backward edge for EVERY physical street.
        # This guarantees an Eulerian Circuit (in-degree == out-degree everywhere).
        G_both_sides = G.to_undirected().to_directed()
        
        circuit = list(nx.eulerian_circuit(G_both_sides))
        
        route_coords = []
        visited_edges = set() 

        for u, v in circuit:
            edge_data = G_both_sides.get_edge_data(u, v)
            
            # Extract geometry if it exists
            if edge_data and 0 in edge_data and 'geometry' in edge_data[0]:
                line = edge_data[0]['geometry']
            else:
                line = LineString([(G_both_sides.nodes[u]['x'], G_both_sides.nodes[u]['y']), 
                                   (G_both_sides.nodes[v]['x'], G_both_sides.nodes[v]['y'])])
            
            coords = list(line.coords)
            
            # Sort the nodes so A->B and B->A are recognized as the same street
            edge_pair = tuple(sorted((u, v)))
            
            # 5. VISUAL OFFSET FIX
            if edge_pair in visited_edges:
                # Offset by roughly 4 meters for the return trip
                offset_coords = [{"lat": y + 0.00004, "lng": x + 0.00004} for x, y in coords]
                route_coords.extend(offset_coords)
            else:
                visited_edges.add(edge_pair)
                normal_coords = [{"lat": y, "lng": x} for x, y in coords]
                route_coords.extend(normal_coords)

        return jsonify(route_coords)

    except Exception as e:
        print(traceback.format_exc())
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
