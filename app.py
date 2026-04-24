import osmnx as ox
import networkx as nx
from flask import Flask, request, jsonify
import urllib.parse

app = Flask(__name__)

@app.route('/get_route', methods=['GET'])
def get_route():
    suburb = request.args.get('suburb', 'Edwardstown')
    
    # 1. Ask OpenStreetMap for the area boundaries
    query = f"{suburb}, South Australia, Australia"
    
    try:
        # Download the walking street network for this exact suburb
        print(f"Downloading graph for {query}...")
        G = ox.graph_from_place(query, network_type='walk')
        
        # 2. The Math (Chinese Postman Problem)
        # Convert to undirected graph so the math works
        G_un = G.to_undirected()
        
        # Eulerize it: This magically adds "phantom" return paths so you don't get stuck
        G_euler = nx.eulerize(G_un)
        
        # Calculate the perfect continuous loop
        circuit = list(nx.eulerian_circuit(G_euler))
        
        # 3. Extract the GPS coordinates from the math nodes
        route_coords = []
        for u, v in circuit:
            node_data = G.nodes[u]
            route_coords.append({
                "lat": node_data['y'], 
                "lng": node_data['x']
            })
            
        return jsonify(route_coords)

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    # Runs locally for testing
    app.run(host='0.0.0.0', port=5000)