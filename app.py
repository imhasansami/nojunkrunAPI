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
        # 1. FIX THE BOUNDARY PROBLEM
        # We grab the shape of the suburb, project it to meters, expand it by 50 meters 
        # to catch all border roads, then convert it back to GPS coordinates.
        gdf = ox.geocode_to_gdf(query)
        gdf_proj = ox.project_gdf(gdf)
        buffered_geom = gdf_proj.geometry.buffer(50) # 50 meter buffer
        gdf_buffered = ox.project_gdf(gdf_proj.set_geometry(buffered_geom), to_latlong=True)
        polygon = gdf_buffered.geometry.iloc[0]
        
        # 2. FIX THE COMMERCIAL PROBLEM
        # This custom filter tells OSM to IGNORE commercial, primary, and secondary roads.
        # It ONLY pulls quiet residential streets and living zones.
        cf = '["highway"~"residential|living_street"]'
        
        # Download the filtered map using our buffered polygon
        G = ox.graph_from_polygon(polygon, network_type='walk', custom_filter=cf, simplify=True)
        
        # 3. FIX THE "WRONG LINES" PROBLEM
        # Sometimes a street is completely cut off from the rest of the neighborhood.
        # This isolates the single largest connected chunk of streets so the math never 
        # has to "jump through a house" to connect a broken road.
        largest_cc = max(nx.connected_components(G), key=len)
        G = G.subgraph(largest_cc).copy()
        
        # 4. THE MATH
        # to_directed() perfectly creates one path forward, and one path back for EVERY street.
        # This is mathematically guaranteed to be a perfect Eulerian circuit.
        G_directed = G.to_directed()
        circuit = list(nx.eulerian_circuit(G_directed))
        
        route_coords = []
        visited_edges = set() # Keep track of what we've walked

        for u, v in circuit:
            edge_data = G_directed.get_edge_data(u, v)
            
            # Extract the curvy geometry of the road
            if edge_data and 0 in edge_data and 'geometry' in edge_data[0]:
                line = edge_data[0]['geometry']
            else:
                line = LineString([(G_directed.nodes[u]['x'], G_directed.nodes[u]['y']), 
                                   (G_directed.nodes[v]['x'], G_directed.nodes[v]['y'])])
            
            coords = list(line.coords)
            edge_pair = tuple(sorted((u, v)))
            
            # 5. FIX THE VISUAL OVERLAP ("Double Lines")
            # If we already walked this street, we offset the GPS coordinates by ~4 meters.
            # This makes the "return trip" draw right next to the original line on your phone,
            # simulating the other side of the street.
            if edge_pair in visited_edges:
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
