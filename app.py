import osmnx as ox
import networkx as nx
from flask import Flask, request, jsonify

app = Flask(__name__)

@app.route('/get_route', methods=['GET'])
def get_route():
    suburb = request.args.get('suburb', 'Edwardstown')
    query = f"{suburb}, South Australia, Australia"
    
    try:
        # Step 1: Download and immediately simplify
        # 'retain_all=False' removes disconnected junk pieces that bloat memory
        G = ox.graph_from_place(query, network_type='walk', retain_all=False, simplify=True)
        
        # Step 2: Greedy Pathfinding (Low RAM)
        # Instead of eulerize, we'll just get the edges and chain them
        route_coords = []
        nodes = list(G.nodes(data=True))
        
        # We'll just traverse the edges in order. It's not a perfect "postman" 
        # route, but it won't crash your server.
        for u, v, data in G.edges(data=True):
            if 'geometry' in data:
                # Extract coordinates from the edge geometry
                x, y = data['geometry'].xy
                for lat, lng in zip(y, x):
                    route_coords.append({"lat": lat, "lng": lng})
            else:
                # Fallback to direct node-to-node
                route_coords.append({"lat": G.nodes[u]['y'], "lng": G.nodes[u]['x']})
                route_coords.append({"lat": G.nodes[v]['y'], "lng": G.nodes[v]['x']})
            
        return jsonify(route_coords)

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
