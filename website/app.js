/**
 * Finland Fleet Dispatcher - Mission Control Simulation Engine
 * Handles CSV parsing, timeline progression, spatial interpolation, and map rendering.
 */

// ==========================================
// 1. STATE & CONSTANTS
// ==========================================
const state = {
  places: {},       // id -> { id, name, lat, lon }
  roads: [],        // [{ from, to, km, minutes }, ...]
  legs: {},         // orderId -> { orderId, pickupPlaceId, deliveryPlaceId, readyAt, pickedUpAt, deliveredAt, isElectric, isPremium }
  gigs: {},         // gigId -> { id, driverId, feeCents, status, postedAt, orderId }
  drivers: {},      // driverId -> { id, home, rating, licence, completedGigs, trips: [] }
  requests: {},     // orderId -> { id, dealerId, arrivedAt, outcome, option, quoteCents }
  events: [],       // sorted by [at, seq]
  
  t: 566,           // Current simulation minute (default Monday morning)
  minT: 0,
  maxT: 14400,      // 10-day horizon (1440 min * 10)
  
  isPlaying: false,
  playbackSpeed: 30, // Simulation minutes per real second
  lastFrameTime: null,
  
  mapView: 'leaflet', // 'leaflet' or 'vector'
  showCars: true,
  showDrivers: true,
  showRoads: true,
  showLabels: true,
  filterQuery: '',
  selectedEntity: null,

  // Leaflet references
  leafletMap: null,
  vehicleLayerGroup: null,
  roadsLayerGroup: null,
  placesLayerGroup: null
};

// Projection for Vector SVG Tactical Map
const FINLAND_BOUNDS = { minLon: 20.8, maxLon: 31.8, minLat: 59.7, maxLat: 67.8 };
function projectGeo(lon, lat) {
  const svgX = ((lon - FINLAND_BOUNDS.minLon) / (FINLAND_BOUNDS.maxLon - FINLAND_BOUNDS.minLon)) * 680 + 130;
  const svgY = 980 - ((lat - FINLAND_BOUNDS.minLat) / (FINLAND_BOUNDS.maxLat - FINLAND_BOUNDS.minLat)) * 890;
  return { x: Math.round(svgX), y: Math.round(svgY) };
}

// Convert simulation minute to Day + HH:MM string
function minuteToDateTime(min) {
  const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun', 'Next Mon', 'Next Tue', 'Next Wed', 'Next Thu'];
  const dayIndex = Math.floor(min / 1440);
  const dayName = days[dayIndex] || `Day ${dayIndex + 1}`;
  const minuteOfDay = min % 1440;
  const hours = Math.floor(minuteOfDay / 60);
  const mins = minuteOfDay % 60;
  const timeStr = `${String(hours).padStart(2, '0')}:${String(mins).padStart(2, '0')}`;
  return { dayName, timeStr, full: `${dayName} ${timeStr}`, dayNum: dayIndex + 1 };
}

// Bearing calculation in degrees between two lat/lon points
function getBearing(lat1, lon1, lat2, lon2) {
  const toRad = deg => (deg * Math.PI) / 180;
  const toDeg = rad => (rad * 180) / Math.PI;
  const y = Math.sin(toRad(lon2 - lon1)) * Math.cos(toRad(lat2));
  const x = Math.cos(toRad(lat1)) * Math.sin(toRad(lat2)) -
            Math.sin(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.cos(toRad(lon2 - lon1));
  let brng = toDeg(Math.atan2(y, x));
  return (brng + 360) % 360;
}

// ==========================================
// 2. CSV PARSER (RFC-4180 COMPLIANT)
// ==========================================
function parseCSV(text) {
  const lines = [];
  let row = [""];
  let inQuotes = false;
  let i = 0;

  while (i < text.length) {
    const char = text[i];
    const nextChar = text[i + 1];

    if (char === '"') {
      if (inQuotes && nextChar === '"') {
        row[row.length - 1] += '"';
        i += 2;
        continue;
      }
      inQuotes = !inQuotes;
    } else if (char === ',' && !inQuotes) {
      row.push("");
    } else if ((char === '\r' || char === '\n') && !inQuotes) {
      if (char === '\r' && nextChar === '\n') i++;
      lines.push(row);
      row = [""];
    } else {
      row[row.length - 1] += char;
    }
    i++;
  }
  if (row.length > 1 || row[0] !== "") lines.push(row);
  if (lines.length < 2) return [];

  const headers = lines[0].map(h => h.trim());
  const records = [];
  for (let r = 1; r < lines.length; r++) {
    const current = lines[r];
    if (current.length === 1 && current[0].trim() === "") continue;
    const obj = {};
    for (let c = 0; c < headers.length; c++) {
      obj[headers[c]] = current[c] !== undefined ? current[c].trim() : "";
    }
    records.push(obj);
  }
  return records;
}

// ==========================================
// 3. INGESTION & DATASET INDEXING
// ==========================================
function indexDataset(raw) {
  // 1. Places
  state.places = {};
  if (raw.places) {
    for (const p of raw.places) {
      if (!p.id) continue;
      state.places[p.id] = {
        id: p.id,
        name: p.name || p.id,
        lat: parseFloat(p.lat),
        lon: parseFloat(p.lon)
      };
    }
  }

  // 2. Roads
  state.roads = [];
  if (raw.roads) {
    for (const r of raw.roads) {
      if (r.from && r.to && r.from !== r.to) {
        state.roads.push({
          from: r.from,
          to: r.to,
          km: parseFloat(r.km || 0),
          minutes: parseFloat(r.minutes || 0)
        });
      }
    }
  }

  // 3. Legs
  state.legs = {};
  if (raw.legs) {
    for (const l of raw.legs) {
      const orderId = l.requestId || l.orderId;
      if (!orderId) continue;
      state.legs[orderId] = {
        orderId: orderId,
        leg: parseInt(l.leg || 0, 10),
        pickupPlaceId: l['pickup.placeId'] || l.pickupPlaceId,
        deliveryPlaceId: l['delivery.placeId'] || l.deliveryPlaceId,
        readyAt: l['truth.readyAt'] ? parseInt(l['truth.readyAt'], 10) : (l.readyAt ? parseInt(l.readyAt, 10) : null),
        pickedUpAt: l['truth.pickedUpAt'] ? parseInt(l['truth.pickedUpAt'], 10) : (l.pickedUpAt ? parseInt(l.pickedUpAt, 10) : null),
        deliveredAt: l['truth.deliveredAt'] ? parseInt(l['truth.deliveredAt'], 10) : (l.deliveredAt ? parseInt(l.deliveredAt, 10) : null),
        isElectric: l['car.electric'] === 'true' || l.isElectric === true,
        isPremium: l['car.premium'] === 'true' || l.isPremium === true
      };
    }
  }

  // 4. Gigs
  state.gigs = {};
  if (raw.gigs) {
    for (const g of raw.gigs) {
      if (!g.id) continue;
      state.gigs[g.id] = {
        id: g.id,
        driverId: g.driverId || null,
        feeCents: parseInt(g.feeCents || 0, 10),
        status: g.status,
        postedAt: parseInt(g.postedAt || 0, 10),
        orderId: g.orders || g.orderId || null
      };
    }
  }

  // 5. Drivers
  state.drivers = {};
  if (raw.drivers) {
    for (const d of raw.drivers) {
      if (!d.id) continue;
      state.drivers[d.id] = {
        id: d.id,
        home: d.homePlaceId || d.home || 'helsinki',
        rating: parseFloat(d.rating || 5.0),
        licence: d.licence || 'manual',
        completedGigs: parseInt(d.completedGigs || 0, 10),
        drops: parseInt(d.drops || 0, 10),
        trips: []
      };
    }
  }

  // 6. Requests
  state.requests = {};
  if (raw.requests) {
    for (const req of raw.requests) {
      if (!req.id) continue;
      const quote = req['same_day.quoteCents'] || req['next_day.quoteCents'] || req['within_3_days.quoteCents'] || req.quoteCents || 0;
      state.requests[req.id] = {
        id: req.id,
        dealerId: req.dealerId,
        arrivedAt: parseInt(req.arrivedAt || 0, 10),
        outcome: req.outcome,
        option: req.option,
        quoteCents: parseInt(quote, 10)
      };
    }
  }

  // 7. Events
  state.events = [];
  if (raw.events) {
    for (const ev of raw.events) {
      const at = parseInt(ev.at || 0, 10);
      const seq = parseInt(ev.seq || 0, 10);
      const kind = ev.kind;
      if (['order_won', 'gig_booked', 'gig_started', 'picked_up', 'delivered', 'no_show', 'order_cancelled'].includes(kind)) {
        state.events.push({
          seq,
          at,
          kind,
          orderId: ev.orderId || ev['order.id'] || null,
          gigId: ev.gigId || null,
          driverId: ev.driverId || null,
          placeId: ev.placeId || null,
          feeCents: ev.feeCents ? parseInt(ev.feeCents, 10) : null,
          priceCents: ev['order.priceCents'] ? parseInt(ev['order.priceCents'], 10) : (ev.priceCents ? parseInt(ev.priceCents, 10) : null)
        });
      }
    }
    state.events.sort((a, b) => a.at - b.at || a.seq - b.seq);
  }

  // Reconstruct Driver Paths & Gigs
  reconstructDriverTrips();

  // Determine timeline bounds
  let minTime = Infinity;
  let maxTime = -Infinity;
  for (const ev of state.events) {
    if (ev.at < minTime) minTime = ev.at;
    if (ev.at > maxTime) maxTime = ev.at;
  }
  for (const leg of Object.values(state.legs)) {
    if (leg.pickedUpAt && leg.pickedUpAt < minTime) minTime = leg.pickedUpAt;
    if (leg.deliveredAt && leg.deliveredAt > maxTime) maxTime = leg.deliveredAt;
  }

  state.minT = minTime !== Infinity ? Math.max(0, minTime - 30) : 0;
  state.maxT = maxTime !== -Infinity ? Math.max(10080, maxTime + 120) : 14400;
  state.t = state.minT;

  const scrubber = document.getElementById('timelineScrubber');
  if (scrubber) {
    scrubber.min = state.minT;
    scrubber.max = state.maxT;
    scrubber.value = state.t;
  }

  // Redraw maps and HUD
  setupLeafletMap();
  renderVectorMap();
  updateSimulation(state.t);

  console.log(`Indexed dataset: ${Object.keys(state.places).length} places, ${state.roads.length} roads, ${Object.keys(state.legs).length} legs, ${Object.keys(state.drivers).length} drivers.`);
}

// Reconstruct continuous movements for each driver
function reconstructDriverTrips() {
  // Map gig events
  const gigEvents = {};
  for (const ev of state.events) {
    if (!ev.gigId) continue;
    if (!gigEvents[ev.gigId]) gigEvents[ev.gigId] = {};
    gigEvents[ev.gigId][ev.kind] = ev;
  }

  for (const gig of Object.values(state.gigs)) {
    if (!gig.driverId || !state.drivers[gig.driverId]) continue;
    const driver = state.drivers[gig.driverId];
    const evs = gigEvents[gig.id] || {};
    const orderLeg = gig.orderId ? state.legs[gig.orderId] : null;

    const startedAt = evs.gig_started ? evs.gig_started.at : (evs.gig_booked ? evs.gig_booked.at : gig.postedAt);
    const pickedUpAt = evs.picked_up ? evs.picked_up.at : (orderLeg ? orderLeg.pickedUpAt : null);
    const deliveredAt = evs.delivered ? evs.delivered.at : (orderLeg ? orderLeg.deliveredAt : null);
    const pickupPlace = (evs.picked_up && evs.picked_up.placeId) || (orderLeg && orderLeg.pickupPlaceId);
    const deliveryPlace = (evs.delivered && evs.delivered.placeId) || (orderLeg && orderLeg.deliveryPlaceId);

    if (pickedUpAt && deliveredAt && pickupPlace && deliveryPlace) {
      driver.trips.push({
        gigId: gig.id,
        orderId: gig.orderId,
        startedAt,
        pickedUpAt,
        deliveredAt,
        pickupPlace,
        deliveryPlace,
        feeCents: gig.feeCents
      });
    }
  }

  // Sort each driver's trips
  for (const driver of Object.values(state.drivers)) {
    driver.trips.sort((a, b) => a.startedAt - b.startedAt);
  }
}

// ==========================================
// 4. LEAFLET MAP INTEGRATION
// ==========================================
function setupLeafletMap() {
  const container = document.getElementById('leafletMapContainer');
  if (!container) return;

  if (state.leafletMap) {
    state.leafletMap.remove();
    state.leafletMap = null;
  }

  // Center on Finland [64.0, 26.0]
  state.leafletMap = L.map('leafletMapContainer', {
    center: [64.2, 26.0],
    zoom: 6,
    minZoom: 5,
    maxZoom: 13,
    zoomControl: false
  });

  // Dark Matter tiles from CartoDB
  L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
    attribution: '&copy; <a href="https://carto.com/">CARTO</a> &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    subdomains: 'abcd',
    maxZoom: 19
  }).addTo(state.leafletMap);

  L.control.zoom({ position: 'topright' }).addTo(state.leafletMap);

  // Layer groups
  state.roadsLayerGroup = L.layerGroup().addTo(state.leafletMap);
  state.placesLayerGroup = L.layerGroup().addTo(state.leafletMap);
  state.vehicleLayerGroup = L.layerGroup().addTo(state.leafletMap);

  drawLeafletRoads();
  drawLeafletPlaces();
}

function drawLeafletRoads() {
  if (!state.roadsLayerGroup) return;
  state.roadsLayerGroup.clearLayers();
  if (!state.showRoads) return;

  const drawnPairs = new Set();
  for (const road of state.roads) {
    const p1 = state.places[road.from];
    const p2 = state.places[road.to];
    if (!p1 || !p2) continue;

    const pairKey = [road.from, road.to].sort().join('--');
    if (drawnPairs.has(pairKey)) continue;
    drawnPairs.add(pairKey);

    L.polyline([[p1.lat, p1.lon], [p2.lat, p2.lon]], {
      color: '#1c2b46',
      weight: 2,
      opacity: 0.65,
      interactive: false
    }).addTo(state.roadsLayerGroup);
  }
}

function drawLeafletPlaces() {
  if (!state.placesLayerGroup) return;
  state.placesLayerGroup.clearLayers();

  const majorCities = new Set(['helsinki', 'tampere', 'turku', 'oulu', 'jyvaskyla', 'kuopio', 'lahti', 'vaasa', 'rovaniemi']);

  for (const place of Object.values(state.places)) {
    const isMajor = majorCities.has(place.id);
    const iconHtml = `
      <div class="city-hub-marker">
        <div class="city-hub-dot ${isMajor ? 'major' : ''}"></div>
        ${state.showLabels ? `<span class="city-hub-label">${place.name}</span>` : ''}
      </div>
    `;

    const customIcon = L.divIcon({
      html: iconHtml,
      className: '',
      iconSize: [60, 20],
      iconAnchor: [30, 6]
    });

    const marker = L.marker([place.lat, place.lon], { icon: customIcon });
    marker.bindPopup(`
      <div class="p-2 text-sm font-sans">
        <div class="font-bold text-primary font-mono text-base uppercase">${place.name} Hub</div>
        <div class="text-xs text-on-surface-variant font-mono mt-1">ID: ${place.id}</div>
        <div class="text-xs text-on-surface-variant font-mono">LAT: ${place.lat.toFixed(4)}, LON: ${place.lon.toFixed(4)}</div>
      </div>
    `);
    marker.addTo(state.placesLayerGroup);
  }
}

// ==========================================
// 5. VECTOR SVG RADAR MAP (STITCH AESTHETIC)
// ==========================================
function renderVectorMap() {
  const svg = document.getElementById('finlandMapSvg');
  if (!svg) return;

  // Draw Roads
  const roadsGroup = document.getElementById('roadsBaseLayer');
  if (roadsGroup) {
    roadsGroup.innerHTML = '';
    const drawnPairs = new Set();
    for (const r of state.roads) {
      const p1 = state.places[r.from];
      const p2 = state.places[r.to];
      if (!p1 || !p2) continue;
      const key = [r.from, r.to].sort().join('--');
      if (drawnPairs.has(key)) continue;
      drawnPairs.add(key);

      const pt1 = projectGeo(p1.lon, p1.lat);
      const pt2 = projectGeo(p2.lon, p2.lat);

      const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
      line.setAttribute('x1', pt1.x);
      line.setAttribute('y1', pt1.y);
      line.setAttribute('x2', pt2.x);
      line.setAttribute('y2', pt2.y);
      line.setAttribute('stroke', '#1c2b46');
      line.setAttribute('stroke-width', '2.5');
      line.setAttribute('stroke-linecap', 'round');
      roadsGroup.appendChild(line);
    }
  }

  // Draw City Nodes
  const nodesGroup = document.getElementById('tacticalNodesGroup');
  if (nodesGroup) {
    nodesGroup.innerHTML = '';
    for (const place of Object.values(state.places)) {
      const pt = projectGeo(place.lon, place.lat);
      const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');

      const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      circle.setAttribute('cx', pt.x);
      circle.setAttribute('cy', pt.y);
      circle.setAttribute('r', '4');
      circle.setAttribute('fill', '#00d2ff');
      circle.setAttribute('stroke', '#050e21');
      circle.setAttribute('stroke-width', '1.5');
      g.appendChild(circle);

      if (state.showLabels) {
        const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        text.setAttribute('x', pt.x + 6);
        text.setAttribute('y', pt.y + 3);
        text.setAttribute('fill', '#a5e7ff');
        text.setAttribute('font-family', 'JetBrains Mono');
        text.setAttribute('font-size', '9');
        text.setAttribute('font-weight', '600');
        text.textContent = place.name;
        g.appendChild(text);
      }
      nodesGroup.appendChild(g);
    }
  }
}

// ==========================================
// 6. SPATIAL APPROXIMATION & VEHICLE MOVEMENT
// ==========================================
function computeActiveTransits(currentMinute) {
  const activeTransits = [];
  let cumulativeDelivered = 0;
  let cumulativeWon = 0;
  let cumulativeRevenue = 0;
  let onTimeDeliveries = 0;

  // Process Orders & Legs
  for (const leg of Object.values(state.legs)) {
    const pickup = state.places[leg.pickupPlaceId];
    const delivery = state.places[leg.deliveryPlaceId];
    if (!pickup || !delivery) continue;

    const req = state.requests[leg.orderId];
    const quoteCents = req ? req.quoteCents : 0;

    if (leg.readyAt && currentMinute >= leg.readyAt) cumulativeWon++;

    if (leg.pickedUpAt && leg.deliveredAt) {
      if (currentMinute >= leg.deliveredAt) {
        cumulativeDelivered++;
        cumulativeRevenue += quoteCents;
        onTimeDeliveries++;
      } else if (currentMinute >= leg.pickedUpAt) {
        // En route!
        const duration = Math.max(1, leg.deliveredAt - leg.pickedUpAt);
        const progress = Math.min(1, Math.max(0, (currentMinute - leg.pickedUpAt) / duration));
        const curLat = pickup.lat + progress * (delivery.lat - pickup.lat);
        const curLon = pickup.lon + progress * (delivery.lon - pickup.lon);
        const bearing = getBearing(pickup.lat, pickup.lon, delivery.lat, delivery.lon);

        activeTransits.push({
          type: 'car',
          orderId: leg.orderId,
          pickupPlaceId: leg.pickupPlaceId,
          pickupName: pickup.name,
          deliveryPlaceId: leg.deliveryPlaceId,
          deliveryName: delivery.name,
          pickedUpAt: leg.pickedUpAt,
          deliveredAt: leg.deliveredAt,
          etaMinutes: Math.max(0, leg.deliveredAt - currentMinute),
          progress: Math.round(progress * 100),
          lat: curLat,
          lon: curLon,
          bearing: bearing,
          isElectric: leg.isElectric,
          isPremium: leg.isPremium,
          quoteCents: quoteCents
        });
      }
    }
  }

  return {
    activeTransits,
    cumulativeDelivered,
    cumulativeWon,
    cumulativeRevenue,
    onTimeDeliveries
  };
}

// Update simulation at minute t
function updateSimulation(t) {
  state.t = t;
  const metrics = computeActiveTransits(t);

  // Filter active transits if search box is filled
  let displayTransits = metrics.activeTransits;
  if (state.filterQuery) {
    const q = state.filterQuery.toLowerCase();
    displayTransits = displayTransits.filter(tr => 
      tr.orderId.toLowerCase().includes(q) ||
      tr.pickupName.toLowerCase().includes(q) ||
      tr.deliveryName.toLowerCase().includes(q)
    );
  }

  // Update HUD & KPI Badges
  updateHUD(metrics, t);

  // Update Clock Rotary Widget
  updateAnalogClock(t);

  // Update Leaflet Markers
  updateLeafletVehicles(displayTransits);

  // Update Vector SVG Vehicles
  updateVectorVehicles(displayTransits);

  // Update Active Transits List
  renderActiveTransitsList(displayTransits);

  // Update Recent Events Log
  renderRecentEvents(t);
}

// Update Leaflet vehicle marker layer
function updateLeafletVehicles(transits) {
  if (!state.vehicleLayerGroup) return;
  state.vehicleLayerGroup.clearLayers();
  if (!state.showCars) return;

  for (const v of transits) {
    const iconClass = v.isElectric ? 'electric' : '';
    const arrowSymbol = '➤';

    const iconHtml = `
      <div class="vehicle-marker-pulse" style="transform: rotate(${v.bearing}deg);">
        <div class="pulse-ring"></div>
        <div class="vehicle-marker-icon ${iconClass}">
          <span style="display:inline-block; transform: rotate(-90deg);">${arrowSymbol}</span>
        </div>
      </div>
    `;

    const customIcon = L.divIcon({
      html: iconHtml,
      className: '',
      iconSize: [28, 28],
      iconAnchor: [14, 14]
    });

    const marker = L.marker([v.lat, v.lon], { icon: customIcon });

    marker.bindPopup(`
      <div class="p-3 font-sans text-sm min-w-[220px]">
        <div class="flex items-center justify-between border-b border-primary/20 pb-1.5 mb-2">
          <span class="font-bold text-primary font-mono text-base">${v.orderId}</span>
          <span class="px-1.5 py-0.5 rounded text-[10px] font-mono ${v.isElectric ? 'bg-secondary/20 text-secondary' : 'bg-primary/20 text-primary'}">
            ${v.isElectric ? 'ELECTRIC' : 'ICE CAR'}
          </span>
        </div>
        <div class="space-y-1 text-xs">
          <div class="flex justify-between text-on-surface">
            <span class="text-on-surface-variant font-mono">Route:</span>
            <span class="font-bold font-mono">${v.pickupName} → ${v.deliveryName}</span>
          </div>
          <div class="flex justify-between text-on-surface">
            <span class="text-on-surface-variant font-mono">ETA:</span>
            <span class="font-bold text-secondary font-mono">${v.etaMinutes} mins remaining</span>
          </div>
          <div class="flex justify-between text-on-surface">
            <span class="text-on-surface-variant font-mono">Progress:</span>
            <span class="font-bold text-primary font-mono">${v.progress}%</span>
          </div>
          <div class="flex justify-between text-on-surface">
            <span class="text-on-surface-variant font-mono">Quote:</span>
            <span class="font-bold text-tertiary font-mono">€${(v.quoteCents / 100).toFixed(2)}</span>
          </div>
        </div>
        <div class="w-full bg-surface-container-highest h-1.5 rounded-full mt-2 overflow-hidden">
          <div class="bg-primary h-full transition-all" style="width: ${v.progress}%"></div>
        </div>
      </div>
    `);

    marker.addTo(state.vehicleLayerGroup);
  }
}

// Update Vector SVG vehicle layer
function updateVectorVehicles(transits) {
  const container = document.getElementById('tacticalVehiclesLayer');
  if (!container) return;
  container.innerHTML = '';
  if (!state.showCars) return;

  for (const v of transits) {
    const pt = projectGeo(v.lon, v.lat);
    const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    g.setAttribute('transform', `translate(${pt.x}, ${pt.y})`);

    // Glowing aura
    const halo = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    halo.setAttribute('r', '8');
    halo.setAttribute('fill', v.isElectric ? '#4edea3' : '#00d2ff');
    halo.setAttribute('opacity', '0.35');
    g.appendChild(halo);

    // Marker dot
    const dot = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    dot.setAttribute('r', '4');
    dot.setAttribute('fill', v.isElectric ? '#4edea3' : '#00d2ff');
    dot.setAttribute('stroke', '#ffffff');
    dot.setAttribute('stroke-width', '1');
    g.appendChild(dot);

    container.appendChild(g);
  }
}

// Update HUD & KPI Badges
function updateHUD(metrics, t) {
  const dt = minuteToDateTime(t);

  const clockEl = document.getElementById('simClockTime');
  if (clockEl) clockEl.textContent = dt.full;

  const minBadge = document.getElementById('simMinuteBadge');
  if (minBadge) minBadge.textContent = `t = ${t} min`;

  const dayTitle = document.getElementById('simDayTitle');
  if (dayTitle) dayTitle.textContent = `DAY ${dt.dayNum} • WEEK DISPATCH`;

  const kpiHauls = document.getElementById('kpiActiveHauls');
  if (kpiHauls) kpiHauls.innerHTML = `${metrics.activeTransits.length} <span class="text-on-surface-variant font-normal text-xs">UNITS</span>`;

  const countEl = document.getElementById('activeListCount');
  if (countEl) countEl.textContent = `${metrics.activeTransits.length} Moving`;

  const kpiDelivered = document.getElementById('kpiDeliveredGigs');
  if (kpiDelivered) kpiDelivered.innerHTML = `${metrics.cumulativeDelivered} <span class="text-on-surface-variant font-normal text-xs">DELIVERED</span>`;

  const kpiRevenue = document.getElementById('kpiCumulativeRevenue');
  if (kpiRevenue) kpiRevenue.textContent = `€${(metrics.cumulativeRevenue / 100).toLocaleString('en-US', { minimumFractionDigits: 0 })}`;

  const onTimeRate = metrics.cumulativeDelivered > 0 ? ((metrics.onTimeDeliveries / metrics.cumulativeDelivered) * 100).toFixed(1) : '100.0';
  const kpiRate = document.getElementById('kpiUtilization');
  if (kpiRate) kpiRate.textContent = `${onTimeRate}%`;
}

// Update Rotary Clock Dial SVG
function updateAnalogClock(t) {
  const minuteOfDay = t % 1440;
  const hourAngle = (minuteOfDay / 1440) * 360;
  const minuteAngle = ((t % 60) / 60) * 360;

  const hourHand = document.getElementById('clockHourHandGroup');
  if (hourHand) hourHand.setAttribute('transform', `rotate(${hourAngle} 60 60)`);

  const minHand = document.getElementById('clockMinuteHandGroup');
  if (minHand) minHand.setAttribute('transform', `rotate(${minuteAngle} 60 60)`);

  const handleGroup = document.getElementById('clockHandleGroup');
  if (handleGroup) handleGroup.setAttribute('transform', `rotate(${hourAngle} 60 60)`);

  const pill = document.getElementById('clockDialAmPmPill');
  if (pill) {
    const dt = minuteToDateTime(t);
    pill.textContent = dt.timeStr;
  }
}

// Render active transits list on the right panel
function renderActiveTransitsList(transits) {
  const container = document.getElementById('activeTransitsList');
  if (!container) return;

  if (transits.length === 0) {
    container.innerHTML = `
      <div class="p-6 text-center text-on-surface-variant text-xs font-mono">
        <span class="material-symbols-outlined text-2xl text-on-surface-variant/40 block mb-1">local_shipping</span>
        No active vehicle transits at minute ${state.t}.<br>Scrub the timeline forward to view shipments.
      </div>
    `;
    return;
  }

  let html = '';
  for (const v of transits) {
    html += `
      <div class="p-3 bg-surface-container rounded-lg border border-white/5 hover:border-primary/40 transition-all cursor-pointer group" onclick="focusOnVehicle('${v.lat}', '${v.lon}')">
        <div class="flex items-center justify-between mb-1.5">
          <span class="font-bold text-primary font-mono text-xs group-hover:text-glow-primary transition-all">${v.orderId}</span>
          <span class="px-1.5 py-0.5 rounded text-[9px] font-mono ${v.isElectric ? 'bg-secondary/20 text-secondary' : 'bg-primary/20 text-primary'}">
            ${v.isElectric ? '⚡ EV' : 'ICE'}
          </span>
        </div>
        <div class="text-[11px] font-mono text-on-surface flex items-center justify-between">
          <span class="text-on-surface">${v.pickupName}</span>
          <span class="material-symbols-outlined text-[13px] text-primary-dim">arrow_forward</span>
          <span class="text-on-surface">${v.deliveryName}</span>
        </div>
        <div class="mt-2 flex items-center justify-between text-[10px] font-mono text-on-surface-variant">
          <span>ETA: <strong class="text-secondary">${v.etaMinutes}m</strong></span>
          <span>Progress: <strong class="text-primary">${v.progress}%</strong></span>
        </div>
        <div class="w-full bg-surface-container-highest h-1 rounded-full mt-1.5 overflow-hidden">
          <div class="bg-primary h-full" style="width: ${v.progress}%"></div>
        </div>
      </div>
    `;
  }
  container.innerHTML = html;
}

// Focus map view on vehicle coordinates
function focusOnVehicle(lat, lon) {
  lat = parseFloat(lat);
  lon = parseFloat(lon);
  if (state.mapView === 'vector') {
    switchMapView('leaflet');
  }
  if (state.leafletMap) {
    state.leafletMap.flyTo([lat, lon], 9, { duration: 0.8 });
  }
}

// Render recent events stream up to minute t
function renderRecentEvents(t) {
  const container = document.getElementById('recentEventsStream');
  if (!container) return;

  // Filter events within last 120 minutes up to t
  const recent = state.events.filter(e => e.at <= t && e.at >= t - 180).slice(-15).reverse();

  if (recent.length === 0) {
    container.innerHTML = `<div class="p-4 text-center text-on-surface-variant text-xs font-mono">No events recorded in the last 3 hours.</div>`;
    return;
  }

  let html = '';
  for (const ev of recent) {
    let icon = 'info';
    let color = 'text-primary';
    let label = ev.kind;

    if (ev.kind === 'picked_up') {
      icon = 'outbox';
      color = 'text-primary';
      label = `Picked up ${ev.orderId} at ${ev.placeId || ''}`;
    } else if (ev.kind === 'delivered') {
      icon = 'task_alt';
      color = 'text-secondary';
      label = `Delivered ${ev.orderId} at ${ev.placeId || ''}`;
    } else if (ev.kind === 'order_won') {
      icon = 'assignment_turned_in';
      color = 'text-tertiary';
      label = `Won Order ${ev.orderId}`;
    } else if (ev.kind === 'gig_started') {
      icon = 'play_arrow';
      color = 'text-primary-dim';
      label = `Driver started gig ${ev.gigId}`;
    } else if (ev.kind === 'no_show') {
      icon = 'warning';
      color = 'text-error';
      label = `No show: gig ${ev.gigId}`;
    }

    const dt = minuteToDateTime(ev.at);
    html += `
      <div class="flex items-start gap-2 py-1.5 border-b border-white/5 text-xs font-mono">
        <span class="material-symbols-outlined text-[15px] ${color} mt-0.5">${icon}</span>
        <div class="flex-1 flex flex-col">
          <span class="text-on-surface text-[11px] leading-tight">${label}</span>
          <span class="text-[9px] text-on-surface-variant">${dt.full} (t = ${ev.at})</span>
        </div>
      </div>
    `;
  }
  container.innerHTML = html;
}

// ==========================================
// 7. ANIMATION PLAYBACK LOOP
// ==========================================
function animationLoop(timestamp) {
  if (!state.isPlaying) return;

  if (state.lastFrameTime === null) {
    state.lastFrameTime = timestamp;
  }

  const deltaSeconds = (timestamp - state.lastFrameTime) / 1000;
  state.lastFrameTime = timestamp;

  // Advance simulation minutes
  const deltaMinutes = deltaSeconds * state.playbackSpeed;
  let nextT = Math.round(state.t + deltaMinutes);

  if (nextT > state.maxT) {
    nextT = state.minT; // loop back
  }

  state.t = nextT;
  const scrubber = document.getElementById('timelineScrubber');
  if (scrubber) scrubber.value = state.t;

  updateSimulation(state.t);
  requestAnimationFrame(animationLoop);
}

function togglePlayPause() {
  state.isPlaying = !state.isPlaying;
  const playBtn = document.getElementById('mainPlayBtn');
  const playIcon = document.getElementById('mainPlayIcon');
  const playText = document.getElementById('mainPlayText');

  if (state.isPlaying) {
    state.lastFrameTime = null;
    if (playIcon) playIcon.textContent = 'pause';
    if (playText) playText.textContent = 'PAUSE';
    if (playBtn) playBtn.classList.replace('bg-primary-container', 'bg-tertiary-container');
    requestAnimationFrame(animationLoop);
  } else {
    if (playIcon) playIcon.textContent = 'play_arrow';
    if (playText) playText.textContent = 'PLAY';
    if (playBtn) playBtn.classList.replace('bg-tertiary-container', 'bg-primary-container');
  }
}

function setSpeedMultiplier(speed) {
  state.playbackSpeed = speed;
  const pills = document.querySelectorAll('#speedPillGroup button');
  pills.forEach(p => {
    if (p.textContent.trim() === `${speed}x`) {
      p.classList.add('bg-primary', 'text-surface', 'font-bold');
      p.classList.remove('text-on-surface');
    } else {
      p.classList.remove('bg-primary', 'text-surface', 'font-bold');
      p.classList.add('text-on-surface');
    }
  });
}

function scrubToMinute(val) {
  val = parseInt(val, 10);
  state.t = Math.max(state.minT, Math.min(state.maxT, val));
  const scrubber = document.getElementById('timelineScrubber');
  if (scrubber) scrubber.value = state.t;
  updateSimulation(state.t);
}

function stepTimeline(delta) {
  scrubToMinute(state.t + delta);
}

function jumpToNextEvent() {
  const next = state.events.find(e => e.at > state.t);
  if (next) scrubToMinute(next.at);
}

// Layer toggles
function toggleMapLayer(layer) {
  if (layer === 'vehicles') {
    state.showCars = !state.showCars;
    const btn = document.getElementById('toggleVehiclesBtn');
    if (btn) btn.classList.toggle('opacity-50');
  } else if (layer === 'roads') {
    state.showRoads = !state.showRoads;
    drawLeafletRoads();
    renderVectorMap();
  } else if (layer === 'labels') {
    state.showLabels = !state.showLabels;
    drawLeafletPlaces();
    renderVectorMap();
  }
  updateSimulation(state.t);
}

// Switch between Leaflet and Vector SVG Map
function switchMapView(view) {
  state.mapView = view;
  const leafletEl = document.getElementById('leafletMapContainer');
  const vectorEl = document.getElementById('finlandMapSvg');
  const btnLeaflet = document.getElementById('btnViewLeaflet');
  const btnVector = document.getElementById('btnViewVector');

  if (view === 'leaflet') {
    if (leafletEl) leafletEl.style.display = 'block';
    if (vectorEl) vectorEl.style.display = 'none';
    if (btnLeaflet) {
      btnLeaflet.className = 'px-2.5 py-1 rounded bg-primary text-surface font-bold transition-all';
    }
    if (btnVector) {
      btnVector.className = 'px-2.5 py-1 rounded text-on-surface-variant hover:text-on-surface transition-all';
    }
    if (state.leafletMap) {
      setTimeout(() => state.leafletMap.invalidateSize(), 50);
    }
  } else {
    if (leafletEl) leafletEl.style.display = 'none';
    if (vectorEl) vectorEl.style.display = 'block';
    if (btnVector) {
      btnVector.className = 'px-2.5 py-1 rounded bg-primary text-surface font-bold transition-all';
    }
    if (btnLeaflet) {
      btnLeaflet.className = 'px-2.5 py-1 rounded text-on-surface-variant hover:text-on-surface transition-all';
    }
  }
}

// ==========================================
// 8. FILE INGESTION (DRAG & DROP / FOLDER)
// ==========================================
function setupFileDrop() {
  const dropzone = document.getElementById('dropzoneModal');
  const fileInput = document.getElementById('folderFileInput');

  window.addEventListener('dragover', (e) => {
    e.preventDefault();
    if (dropzone) dropzone.classList.remove('hidden');
  });

  if (dropzone) {
    dropzone.addEventListener('dragleave', (e) => {
      if (e.target === dropzone) dropzone.classList.add('hidden');
    });

    dropzone.addEventListener('drop', async (e) => {
      e.preventDefault();
      dropzone.classList.add('hidden');
      const items = e.dataTransfer.items;
      if (items) {
        const files = [];
        for (let i = 0; i < items.length; i++) {
          const entry = items[i].webkitGetAsEntry ? items[i].webkitGetAsEntry() : null;
          if (entry) {
            await scanEntry(entry, files);
          } else {
            const f = items[i].getAsFile();
            if (f) files.push(f);
          }
        }
        processUploadedFiles(files);
      }
    });
  }

  if (fileInput) {
    fileInput.addEventListener('change', (e) => {
      if (e.target.files && e.target.files.length > 0) {
        processUploadedFiles(Array.from(e.target.files));
      }
    });
  }
}

async function scanEntry(entry, resultFiles) {
  if (entry.isFile) {
    const file = await new Promise(resolve => entry.file(resolve));
    resultFiles.push(file);
  } else if (entry.isDirectory) {
    const reader = entry.createReader();
    const entries = await new Promise(resolve => reader.readEntries(resolve));
    for (const child of entries) {
      await scanEntry(child, resultFiles);
    }
  }
}

async function processUploadedFiles(files) {
  const rawData = {
    places: null,
    roads: null,
    legs: null,
    gigs: null,
    drivers: null,
    requests: null,
    events: null
  };

  for (const file of files) {
    const name = file.name.toLowerCase();
    const text = await file.text();
    if (name.includes('places.csv')) rawData.places = parseCSV(text);
    else if (name.includes('roads.csv')) rawData.roads = parseCSV(text);
    else if (name.includes('legs.csv')) rawData.legs = parseCSV(text);
    else if (name.includes('gigs.csv')) rawData.gigs = parseCSV(text);
    else if (name.includes('drivers.csv')) rawData.drivers = parseCSV(text);
    else if (name.includes('requests.csv')) rawData.requests = parseCSV(text);
    else if (name.includes('events.csv')) rawData.events = parseCSV(text);
  }

  if (rawData.places || rawData.legs || rawData.events) {
    indexDataset(rawData);
    showNotification(`Dataset loaded successfully from ${files.length} files.`);
  } else {
    alert("Could not recognize dispatch CSV files in the uploaded set. Please include places.csv, legs.csv, events.csv, etc.");
  }
}

function showNotification(msg) {
  const notif = document.getElementById('hudNotification');
  if (notif) {
    notif.textContent = msg;
    notif.classList.remove('opacity-0', 'pointer-events-none');
    setTimeout(() => {
      notif.classList.add('opacity-0', 'pointer-events-none');
    }, 4000);
  }
}

// ==========================================
// 9. INITIALIZATION
// ==========================================
document.addEventListener('DOMContentLoaded', () => {
  setupFileDrop();

  // Search input handler
  const searchInput = document.getElementById('searchVehicleInput');
  if (searchInput) {
    searchInput.addEventListener('input', (e) => {
      state.filterQuery = e.target.value;
      updateSimulation(state.t);
    });
  }

  // Load default dataset (precompiled from practice_level_1)
  if (window.DEFAULT_DATASET) {
    console.log("Loading precompiled practice_level_1 dataset...");
    indexDataset(window.DEFAULT_DATASET);
  } else {
    console.warn("No default dataset found. Ready for file ingestion.");
  }
});
