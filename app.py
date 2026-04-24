import osmnx as ox
import networkx as nx
from flask import Flask, request, jsonify
import traceback

app = Flask(__name__)

@app.route('/get_route', methods=['GET'])
def get_route():
    suburb = request.args.get('suburb', 'Edwardstown')
    query = f"{suburb}, South Australia, Australia"
    
    try:
        # Increase timeout for slow OSM responses
        ox.settings.timeout = 120 
        
        # Download graph
        print(f"Fetching: {query}")
        G = ox.graph_from_place(query, network_type='walk')
        
        # Math: Eulerian Circuit (Chinese Postman)
        G_un = G.to_undirected()
        G_euler = nx.eulerize(G_un)
        circuit = list(nx.eulerian_circuit(G_euler))
        
        route_coords = []
        for u, v in circuit:
            node_data = G.nodes[u]
            route_coords.append({"lat": node_data['y'], "lng": node_data['x']})
            
        return jsonify(route_coords)

    except Exception as e:
        # This sends the REAL error back to your Android app
        error_msg = f"Crash: {str(e)}"
        print(traceback.format_exc())
        return jsonify({"error": error_msg}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
