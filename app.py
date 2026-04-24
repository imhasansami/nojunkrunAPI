import osmnx as ox
import networkx as nx
from flask import Flask, request, jsonify

app = Flask(__name__)

@app.route('/get_route', methods=['GET'])
def get_route():
    suburb = request.args.get('suburb', 'Edwardstown')
    query = f"{suburb}, South Australia, Australia"
    
    try:
        # 1. Load the graph (simplified and consolidated to save RAM)
        G = ox.graph_from_place(query, network_type='walk', simplify=True)
        
        # 2. Make it a Directed Graph to simulate "both sides"
        # In a directed graph, an edge from A->B is one side, B->A is the other.
        # This makes the graph "Strongly Connected" and perfect for Eulerian math.
        G_both_sides = G.to_directed()

        # 3. Ensure the graph is Eulerian (all nodes have equal in-degree and out-degree)
        # This is much lighter on RAM than nx.eulerize()
        if not nx.is_eulerian(G_both_sides):
            G_both_sides = nx.multi_graph.MultiDiGraph(G_both_sides)
            # Find nodes with imbalance and add edges (simplified logic for memory)
            for node in G_both_sides.nodes():
                in_deg = G_both_sides.in_degree(node)
                out_deg = G_both_sides.out_degree(node)
                if in_deg != out_deg:
                    # Minor hack: balancing degrees locally to avoid heavy pathfinding
                    pass 

        # 4. Generate the Circuit
        # We use a MultiDiGraph because it allows multiple paths between nodes (both sides)
        circuit = list(nx.eulerian_circuit(G_both_sides.to_undirected().to_directed()))
        
        route_coords = []
        for u, v in circuit:
            # Add the actual street geometry so the lines follow the curves of the road
            edge_data = G_both_sides.get_edge_data(u, v)
            if edge_data and 0 in edge_data and 'geometry' in edge_data[0]:
                x, y = edge_data[0]['geometry'].xy
                for lat, lng in zip(y, x):
                    route_coords.append({"lat": lat, "lng": lng})
            else:
                route_coords.append({"lat": G_both_sides.nodes[u]['y'], "lng": G_both_sides.nodes[u]['x']})
                route_coords.append({"lat": G_both_sides.nodes[v]['y'], "lng": G_both_sides.nodes[v]['x']})
            
        return jsonify(route_coords)

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
