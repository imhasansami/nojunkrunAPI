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
    query = f"{suburb}, South Australia, Australia"
    
    try:
        # 1. GET EXACT BOUNDARY
        gdf = ox.geocode_to_gdf(query)
        polygon = gdf.geometry.iloc[0]
        boundary_line = polygon.boundary
        
        # 2. FETCH GRAPH
        # Expanded buffer to 0.002 to ensure we catch the centerlines of massive main roads
        buffered_poly = polygon.buffer(0.002) 
        
        # FIX: Added 'secondary' and 'primary' to the filter. 
        # Without this, main boundary roads get deleted immediately.
        cf = '["highway"~"residential|living_street|unclassified|tertiary|secondary|primary"]'
        
        G = ox.graph_from_polygon(buffered_poly, network_type='walk', custom_filter=cf, simplify=True)
        
        # 3. CLEAN GRAPH (Remove spiderwebs)
        G_un = ox.utils_graph.get_undirected(G)
        largest_cc = max(nx.connected_components(G_un), key=len)
        G_clean = G_un.subgraph(largest_cc).copy()

        # 4. MASTER ROUTING GRAPH (1-side vs 2-side)
        G_route = nx.MultiGraph()
        
        # Increased tolerance. Main roads are wide; their center is further from the border.
        BOUNDARY_TOLERANCE = 0.0006 
        
        for u, v, key, data in G_clean.edges(keys=True, data=True):
            if 'geometry' in data:
                geom = data['geometry']
            else:
                geom = LineString([(G_clean.nodes[u]['x'], G_clean.nodes[u]['y']), 
                                   (G_clean.nodes[v]['x'], G_clean.nodes[v]['y'])])
                data['geometry'] = geom

            is_boundary = geom.distance(boundary_line) < BOUNDARY_TOLERANCE 
            
            G_route.add_node(u, x=G_clean.nodes[u]['x'], y=G_clean.nodes[u]['y'])
            G_route.add_node(v, x=G_clean.nodes[v]['x'], y=G_clean.nodes[v]['y'])
            
            if is_boundary:
                G_route.add_edge(u, v, **data) # Walk once on border
            else:
                G_route.add_edge(u, v, **data) # Walk down
                G_route.add_edge(u, v, **data) # Walk back
                
        # 5. EULERIZE
        G_euler = nx.eulerize(G_route)
        circuit = list(nx.eulerian_circuit(G_euler))
        
        # 6. VECTOR SHIFTING (Double lines)
        route_coords = []
        
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

            offset_coords = []
            for i in range(len(coords) - 1):
                x1, y1 = coords[i]
                x2, y2 = coords[i+1]
                
                dx = x2 - x1
                dy = y2 - y1
                length = math.hypot(dx, dy)
                
                if length == 0:
                    continue
                    
                nx_vec = dy / length
                ny_vec = -dx / length
                
                offset = 0.00003
                
                new_x1 = x1 + (nx_vec * offset)
                new_y1 = y1 + (ny_vec * offset)
                
                if i == 0:
                    offset_coords.append({"lat": new_y1, "lng": new_x1})
                    
                new_x2 = x2 + (nx_vec * offset)
                new_y2 = y2 + (ny_vec * offset)
                offset_coords.append({"lat": new_y2, "lng": new_x2})
                
            route_coords.extend(offset_coords)

        # 7. EXPORT LOGIC (Handles both App Lines and KML Download)
        format_type = request.args.get('format', 'json')
        
        if format_type == 'kml':
            kml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
            <kml xmlns="http://www.opengis.net/kml/2.2">
              <Document>
                <name>{suburb} Route</name>
                <Placemark>
                  <LineString>
                    <coordinates>"""
            
            for pt in route_coords:
                kml_content += f"{pt['lng']},{pt['lat']},0 "
                
            kml_content += """</coordinates>
                  </LineString>
                </Placemark>
              </Document>
            </kml>"""
            
            safe_filename = suburb.replace(' ', '_') + "_route.kml"
            
            return Response(
                kml_content, 
                mimetype='application/vnd.google-earth.kml+xml',
                headers={"Content-disposition": f"attachment; filename={safe_filename}"}
            )
        else:
            return jsonify(route_coords)

    except Exception as e:
        print(traceback.format_exc())
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
