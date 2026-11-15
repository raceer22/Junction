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
  wonPrices: {},    // orderId -> priceCents from order_won
  events: [],       // operational events sorted by [at, seq]
  ledgerEvents: [], // ledger money-in/out events sorted by [at, seq]
  gigCosts: [],     // fallback cost entries [{ at, cents }] when ledgerEvents are absent
  ordersMeta: {},   // orderId -> { orderId, wonAt, deadline, numLegs, cancelled, cancelledAt, deliveredAtByLeg }
  endEvalT: 14400,  // end-of-simulation timestamp for final settlement
  simOutcome: null, // { marginCents, onTimePct, wonOrders, failed, warningMessage }
  
  t: 566,           // Current simulation minute (default Monday morning)
  minT: 0,
  maxT: 14400,      // 10-day horizon (1440 min * 10)
  
  isPlaying: false,
  playbackSpeed: 30, // Simulation minutes per real second
  lastFrameTime: null,
  
  mapView: 'leaflet', // 'leaflet' or 'vector'
  showCars: true,
  showDrivers: true,
  showLabels: true,
  filterQuery: '',
  selectedEntity: null,

  // Leaflet references
  leafletMap: null,
  vehicleLayerGroup: null,
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
function formatEurosTwoDecimals(cents) {
  const absEuros = Math.abs(cents) / 100;
  const formatted = absEuros.toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  });
  return cents < 0 ? `-€${formatted}` : `€${formatted}`;
}

function formatRatePct(pct, isBelowMinimum = false) {
  if (isBelowMinimum && pct.toFixed(2) === '90.00') {
    return (Math.floor(pct * 100) / 100).toFixed(2);
  }
  return pct.toFixed(2);
}

function evaluateSimulationOutcome(finalMarginCents, finalOnTimePct, wonOrders) {
  const marginFailed = finalMarginCents < 0;
  const deliveryFailed = finalOnTimePct < 90.0 - 1e-9;
  const reasons = [];
  if (marginFailed) {
    reasons.push(`negative margin (${formatEurosTwoDecimals(finalMarginCents)})`);
  }
  if (deliveryFailed) {
    reasons.push(`delivery rate ${formatRatePct(finalOnTimePct, true)}% (< 90.00%)`);
  }
  const failed = reasons.length > 0;
  return {
    marginCents: finalMarginCents,
    onTimePct: finalOnTimePct,
    wonOrders,
    marginFailed,
    deliveryFailed,
    failed,
    warningMessage: failed ? `Simulation failed: ${reasons.join(' & ')}` : ''
  };
}

function renderSimFailureWarning(outcome) {
  const box = document.getElementById('simFailureWarningBox');
  const textEl = document.getElementById('simFailureWarningText');
  if (!box || !textEl) return;
  if (outcome && outcome.failed) {
    textEl.textContent = outcome.warningMessage;
    box.classList.remove('hidden');
    box.classList.add('flex');
  } else {
    textEl.textContent = '';
    box.classList.add('hidden');
    box.classList.remove('flex');
  }
}

function indexDataset(raw, serverScore = null) {
  const toList = (val) => (Array.isArray(val) ? val : (val && typeof val === 'object' ? Object.values(val) : []));
  state.simOutcome = null;

  // 1. Places
  state.places = {};
  for (const p of toList(raw.places)) {
    if (!p.id) continue;
    state.places[p.id] = {
      id: p.id,
      name: p.name || p.id,
      lat: parseFloat(p.lat),
      lon: parseFloat(p.lon)
    };
  }

  // 2. Roads
  state.roads = [];
  for (const r of toList(raw.roads)) {
    if (r.from && r.to && r.from !== r.to) {
      state.roads.push({
        from: r.from,
        to: r.to,
        km: parseFloat(r.km || 0),
        minutes: parseFloat(r.minutes || 0)
      });
    }
  }

  // 3. Legs
  state.legs = {};
  const legCountByOrder = {};
  const legTruthDeliveredByOrder = {};
  for (const l of toList(raw.legs)) {
    const orderId = l.requestId || l.orderId;
    if (!orderId) continue;
    const rawLegIdx = parseInt(l.leg || 0, 10);
    const safeLegIdx = Number.isFinite(rawLegIdx) && rawLegIdx >= 0 ? rawLegIdx : 0;
    legCountByOrder[orderId] = Math.max(legCountByOrder[orderId] || 0, safeLegIdx + 1);
    const readyAt = l['truth.readyAt'] ? parseInt(l['truth.readyAt'], 10) : (l.readyAt ? parseInt(l.readyAt, 10) : null);
    const pickedUpAt = l['truth.pickedUpAt'] ? parseInt(l['truth.pickedUpAt'], 10) : (l.pickedUpAt ? parseInt(l.pickedUpAt, 10) : null);
    const deliveredAt = l['truth.deliveredAt'] ? parseInt(l['truth.deliveredAt'], 10) : (l.deliveredAt ? parseInt(l.deliveredAt, 10) : null);
    if (Number.isFinite(deliveredAt)) {
      if (!legTruthDeliveredByOrder[orderId]) legTruthDeliveredByOrder[orderId] = {};
      legTruthDeliveredByOrder[orderId][safeLegIdx] = deliveredAt;
    }
    state.legs[orderId] = {
      orderId: orderId,
      leg: safeLegIdx,
      pickupPlaceId: l['pickup.placeId'] || l.pickupPlaceId,
      deliveryPlaceId: l['delivery.placeId'] || l.deliveryPlaceId,
      readyAt,
      pickedUpAt,
      deliveredAt,
      isElectric: l['car.electric'] === 'true' || l.isElectric === true,
      isPremium: l['car.premium'] === 'true' || l.isPremium === true
    };
  }

  // 4. Gigs
  state.gigs = {};
  for (const g of toList(raw.gigs)) {
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

  // 5. Drivers
  state.drivers = {};
  for (const d of toList(raw.drivers)) {
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

  // 6. Requests
  state.requests = {};
  for (const req of toList(raw.requests)) {
    if (!req.id) continue;
    const optionKey = req.option ? `${req.option}.quoteCents` : null;
    const quote = (optionKey && req[optionKey] !== undefined && req[optionKey] !== '')
      ? req[optionKey]
      : (req.quoteCents || req['same_day.quoteCents'] || req['next_day.quoteCents'] || req['within_3_days.quoteCents'] || 0);
    state.requests[req.id] = {
      id: req.id,
      dealerId: req.dealerId,
      arrivedAt: parseInt(req.arrivedAt || 0, 10),
      outcome: req.outcome,
      option: req.option,
      quoteCents: parseInt(quote, 10) || 0
    };
  }

  // 7. Events & Ledger
  state.events = [];
  state.ledgerEvents = [];
  state.wonPrices = {};
  state.ordersMeta = {};
  const gigDeliveredAt = {};
  const gigBookedFee = {};

  const parseNumLegsFromEvent = (ev, oid) => {
    const rawLegs = ev['order.legs'] !== undefined ? ev['order.legs'] : (ev.order && ev.order.legs);
    if (Array.isArray(rawLegs) && rawLegs.length > 0) return rawLegs.length;
    if (typeof rawLegs === 'string' && rawLegs.trim() !== '') {
      try {
        const parsed = JSON.parse(rawLegs);
        if (Array.isArray(parsed) && parsed.length > 0) return parsed.length;
      } catch (_) {
        // Ignore malformed JSON and fall back to legs.csv count
      }
    }
    return legCountByOrder[oid] || 1;
  };

  const parseDeadlineFromEvent = (ev) => {
    const rawDeadline = ev['order.deadline'] !== undefined && ev['order.deadline'] !== ''
      ? ev['order.deadline']
      : (ev.order && ev.order.deadline !== undefined ? ev.order.deadline : ev.deadline);
    if (rawDeadline === undefined || rawDeadline === null || rawDeadline === '') return null;
    const parsed = parseInt(rawDeadline, 10);
    return Number.isFinite(parsed) ? parsed : null;
  };

  const ensureOrderMeta = (oid, defaultWonAt = 0) => {
    if (!state.ordersMeta[oid]) {
      state.ordersMeta[oid] = {
        orderId: oid,
        wonAt: defaultWonAt,
        deadline: null,
        numLegs: legCountByOrder[oid] || 1,
        cancelled: false,
        cancelledAt: null,
        deliveredAtByLeg: {}
      };
    }
    return state.ordersMeta[oid];
  };

  for (const ev of toList(raw.events)) {
    const at = parseInt(ev.at || 0, 10);
    const seq = parseInt(ev.seq || 0, 10);
    const kind = ev.kind;

    if (kind === 'ledger') {
      const rawCents = ev['line.cents'] !== undefined && ev['line.cents'] !== ''
        ? ev['line.cents']
        : (ev.line && ev.line.cents !== undefined ? ev.line.cents : ev.cents);
      const cents = parseInt(rawCents, 10);
      if (Number.isFinite(cents)) {
        state.ledgerEvents.push({
          seq,
          at,
          ledgerKind: ev['line.kind'] || (ev.line && ev.line.kind) || ev.ledgerKind || '',
          cents,
          orderId: ev['line.orderId'] || (ev.line && ev.line.orderId) || ev.orderId || null,
          gigId: ev['line.gigId'] || (ev.line && ev.line.gigId) || ev.gigId || null
        });
      }
      continue;
    }

    if (kind === 'order_changed') {
      const changedOrderId = ev['order.id'] || (ev.order && ev.order.id) || ev.orderId || null;
      if (changedOrderId) {
        const meta = ensureOrderMeta(changedOrderId, at);
        const newDeadline = parseDeadlineFromEvent(ev);
        if (newDeadline !== null) meta.deadline = newDeadline;
        meta.numLegs = parseNumLegsFromEvent(ev, changedOrderId);
        const changedPrice = ev['order.priceCents'] !== undefined && ev['order.priceCents'] !== ''
          ? parseInt(ev['order.priceCents'], 10)
          : (ev.order && Number.isFinite(ev.order.priceCents) ? ev.order.priceCents : null);
        if (Number.isFinite(changedPrice)) {
          state.wonPrices[changedOrderId] = changedPrice;
        }
      }
      continue;
    }

    if (['order_won', 'gig_booked', 'gig_started', 'picked_up', 'delivered', 'no_show', 'order_cancelled'].includes(kind)) {
      const orderId = ev.orderId || ev['order.id'] || (ev.order && ev.order.id) || null;
      const gigId = ev.gigId || null;
      const feeCents = ev.feeCents !== undefined && ev.feeCents !== null && ev.feeCents !== '' ? parseInt(ev.feeCents, 10) : null;
      const priceCents = ev['order.priceCents'] !== undefined && ev['order.priceCents'] !== null && ev['order.priceCents'] !== ''
        ? parseInt(ev['order.priceCents'], 10)
        : (ev.priceCents !== undefined && ev.priceCents !== null && ev.priceCents !== '' ? parseInt(ev.priceCents, 10) : null);

      if (kind === 'order_won' && orderId) {
        if (Number.isFinite(priceCents)) {
          state.wonPrices[orderId] = priceCents;
        }
        const meta = ensureOrderMeta(orderId, at);
        meta.wonAt = at;
        const parsedDeadline = parseDeadlineFromEvent(ev);
        if (parsedDeadline !== null) meta.deadline = parsedDeadline;
        meta.numLegs = parseNumLegsFromEvent(ev, orderId);
      }
      if (kind === 'order_cancelled' && orderId) {
        const meta = ensureOrderMeta(orderId, at);
        meta.cancelled = true;
        meta.cancelledAt = at;
      }
      if (kind === 'gig_booked' && gigId && Number.isFinite(feeCents)) {
        gigBookedFee[gigId] = feeCents;
      }
      if (kind === 'delivered') {
        if (gigId) {
          gigDeliveredAt[gigId] = Math.max(gigDeliveredAt[gigId] || 0, at);
        }
        if (orderId) {
          const meta = ensureOrderMeta(orderId, at);
          const rawLeg = ev.leg !== undefined && ev.leg !== '' ? parseInt(ev.leg, 10) : 0;
          const safeLeg = Number.isFinite(rawLeg) && rawLeg >= 0 ? rawLeg : 0;
          meta.deliveredAtByLeg[safeLeg] = at;
        }
      }

      state.events.push({
        seq,
        at,
        kind,
        orderId,
        gigId,
        driverId: ev.driverId || null,
        placeId: ev.placeId || null,
        feeCents,
        priceCents
      });
    }
  }
  state.events.sort((a, b) => a.at - b.at || a.seq - b.seq);
  state.ledgerEvents.sort((a, b) => a.at - b.at || a.seq - b.seq);

  // Fallback won-order & delivery population for precompiled/stripped CSV sets
  for (const req of Object.values(state.requests)) {
    if (req.outcome === 'won' && !state.ordersMeta[req.id]) {
      ensureOrderMeta(req.id, req.arrivedAt || 0);
    }
  }
  for (const meta of Object.values(state.ordersMeta)) {
    const truthMap = legTruthDeliveredByOrder[meta.orderId];
    if (truthMap) {
      for (const [legIdxStr, truthAt] of Object.entries(truthMap)) {
        const idx = parseInt(legIdxStr, 10);
        if (meta.deliveredAtByLeg[idx] === undefined) {
          meta.deliveredAtByLeg[idx] = truthAt;
        }
      }
    }
  }

  // Precompute fallback gig fee costs (paid when a gig completes) for datasets without ledger events
  state.gigCosts = [];
  const countedGigs = new Set();
  for (const g of Object.values(state.gigs)) {
    const completedAt = gigDeliveredAt[g.id] ?? (g.status === 'done' && g.orderId ? state.legs[g.orderId]?.deliveredAt : null);
    if (completedAt !== undefined && completedAt !== null && (g.status === 'done' || gigDeliveredAt[g.id] !== undefined)) {
      const fee = g.feeCents || gigBookedFee[g.id] || 0;
      if (fee > 0) {
        state.gigCosts.push({ at: completedAt, cents: fee });
        countedGigs.add(g.id);
      }
    }
  }
  for (const [gigId, completedAt] of Object.entries(gigDeliveredAt)) {
    if (!countedGigs.has(gigId) && gigBookedFee[gigId] > 0) {
      state.gigCosts.push({ at: completedAt, cents: gigBookedFee[gigId] });
    }
  }
  state.gigCosts.sort((a, b) => a.at - b.at);

  // Reconstruct Driver Paths & Gigs
  reconstructDriverTrips();

  // Determine timeline bounds
  let minTime = Infinity;
  let maxTime = -Infinity;
  for (const ev of state.events) {
    if (ev.at < minTime) minTime = ev.at;
    if (ev.at > maxTime) maxTime = ev.at;
  }
  for (const ev of state.ledgerEvents) {
    if (ev.at < minTime) minTime = ev.at;
    if (ev.at > maxTime) maxTime = ev.at;
  }
  for (const leg of Object.values(state.legs)) {
    if (leg.pickedUpAt && leg.pickedUpAt < minTime) minTime = leg.pickedUpAt;
    if (leg.deliveredAt && leg.deliveredAt > maxTime) maxTime = leg.deliveredAt;
  }

  state.minT = minTime !== Infinity ? Math.max(0, minTime - 30) : 0;
  state.maxT = maxTime !== -Infinity ? Math.max(10080, maxTime + 120) : 14400;
  state.endEvalT = maxTime !== -Infinity ? maxTime : state.maxT;
  state.t = state.minT;

  const scrubber = document.getElementById('timelineScrubber');
  if (scrubber) {
    scrubber.min = state.minT;
    scrubber.max = state.maxT;
    scrubber.value = state.t;
  }

  // Evaluate end-of-simulation check (delivery rate < 90.00% or negative margin)
  let finalMarginCents = 0;
  let finalOnTimePct = 0.0;
  let wonOrdersCount = 0;

  if (
    serverScore &&
    typeof serverScore === 'object' &&
    Number.isFinite(serverScore.marginCents) &&
    Number.isFinite(serverScore.onTimeShare)
  ) {
    wonOrdersCount = Number.isFinite(serverScore.wonOrders) ? serverScore.wonOrders : 0;
    finalMarginCents = Math.round(serverScore.marginCents);
    finalOnTimePct = wonOrdersCount > 0 ? serverScore.onTimeShare * 100 : 0.0;
  } else {
    const activeWonOrders = Object.values(state.ordersMeta).filter(m => !m.cancelled);
    wonOrdersCount = activeWonOrders.length;
    let onTimeCount = 0;
    for (const meta of activeWonOrders) {
      let allDelivered = true;
      let maxDeliv = -Infinity;
      for (let i = 0; i < meta.numLegs; i++) {
        const dAt = meta.deliveredAtByLeg[i];
        if (!Number.isFinite(dAt)) {
          allDelivered = false;
          break;
        }
        if (dAt > maxDeliv) maxDeliv = dAt;
      }
      if (allDelivered && (meta.deadline === null || maxDeliv <= meta.deadline)) {
        onTimeCount++;
      }
    }
    finalOnTimePct = wonOrdersCount > 0 ? (onTimeCount / wonOrdersCount) * 100 : 0.0;

    if (state.ledgerEvents.length > 0) {
      finalMarginCents = state.ledgerEvents.reduce((sum, ev) => sum + ev.cents, 0);
    } else {
      finalMarginCents = computeActiveTransits(state.maxT).cumulativeMargin;
    }
  }

  state.simOutcome = evaluateSimulationOutcome(finalMarginCents, finalOnTimePct, wonOrdersCount);
  renderSimFailureWarning(state.simOutcome);

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

  // Map tile layer (reads optional API key & tile URL from window.APP_CONFIG in website/config.js)
  const appConfig = window.APP_CONFIG || {};
  const apiKey = (appConfig.MAP_API_KEY || '').trim();
  let tileUrl = appConfig.MAP_TILE_URL || 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png';
  if (apiKey) {
    const encodedKey = encodeURIComponent(apiKey);
    if (tileUrl.includes('{apiKey}')) {
      tileUrl = tileUrl.replace('{apiKey}', encodedKey);
    } else {
      const separator = tileUrl.includes('?') ? '&' : '?';
      tileUrl = `${tileUrl}${separator}key=${encodedKey}`;
    }
  }


  L.tileLayer(tileUrl, {
    attribution: appConfig.MAP_TILE_ATTRIBUTION || '&copy; <a href="https://carto.com/">CARTO</a> &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    subdomains: appConfig.MAP_TILE_SUBDOMAINS || 'abcd',
    maxZoom: appConfig.MAP_TILE_MAX_ZOOM || 19
  }).addTo(state.leafletMap);

  L.control.zoom({ position: 'topright' }).addTo(state.leafletMap);

  // Layer groups
  state.placesLayerGroup = L.layerGroup().addTo(state.leafletMap);
  state.vehicleLayerGroup = L.layerGroup().addTo(state.leafletMap);

  drawLeafletPlaces();
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
function computeOrderOnTimePct(currentMinute) {
  if (state.simOutcome && currentMinute >= state.endEvalT) {
    return state.simOutcome.onTimePct;
  }
  let settled = 0;
  let onTime = 0;
  for (const meta of Object.values(state.ordersMeta)) {
    if (meta.wonAt > currentMinute) continue;
    if (meta.cancelled && (meta.cancelledAt === null || meta.cancelledAt <= currentMinute)) continue;
    let allDelivered = true;
    let maxDeliv = -Infinity;
    for (let i = 0; i < meta.numLegs; i++) {
      const dAt = meta.deliveredAtByLeg[i];
      if (!Number.isFinite(dAt) || dAt > currentMinute) {
        allDelivered = false;
        break;
      }
      if (dAt > maxDeliv) maxDeliv = dAt;
    }
    if (allDelivered) {
      settled++;
      if (meta.deadline === null || maxDeliv <= meta.deadline) {
        onTime++;
      }
    } else if (meta.deadline !== null && currentMinute > meta.deadline) {
      settled++;
    }
  }
  if (settled === 0) {
    return (state.simOutcome && state.simOutcome.wonOrders === 0) ? 0.0 : 100.0;
  }
  return (onTime / settled) * 100;
}

function computeActiveTransits(currentMinute) {
  const activeTransits = [];
  let cumulativeDelivered = 0;
  let cumulativeWon = 0;
  let cumulativeRevenue = 0;

  // Process Orders & Legs
  for (const leg of Object.values(state.legs)) {
    const pickup = state.places[leg.pickupPlaceId];
    const delivery = state.places[leg.deliveryPlaceId];
    if (!pickup || !delivery) continue;

    const req = state.requests[leg.orderId];
    const quoteCents = (req && req.quoteCents) ? req.quoteCents : (state.wonPrices[leg.orderId] || 0);

    if (leg.readyAt && currentMinute >= leg.readyAt) cumulativeWon++;

    if (leg.pickedUpAt && leg.deliveredAt) {
      if (currentMinute >= leg.deliveredAt) {
        cumulativeDelivered++;
        cumulativeRevenue += quoteCents;
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

  // Compute cumulative costs & margin = revenue - costs
  let cumulativeCosts = 0;
  let cumulativeMargin = 0;

  if (state.ledgerEvents.length > 0) {
    let ledgerRevenue = 0;
    for (const ev of state.ledgerEvents) {
      if (ev.at > currentMinute) break;
      if (ev.cents >= 0) {
        ledgerRevenue += ev.cents;
      } else {
        cumulativeCosts += -ev.cents;
      }
    }
    cumulativeRevenue = ledgerRevenue;
    cumulativeMargin = ledgerRevenue - cumulativeCosts;
  } else {
    for (const cost of state.gigCosts) {
      if (cost.at > currentMinute) break;
      cumulativeCosts += cost.cents;
    }
    cumulativeMargin = cumulativeRevenue - cumulativeCosts;
  }

  const onTimePct = computeOrderOnTimePct(currentMinute);

  return {
    activeTransits,
    cumulativeDelivered,
    cumulativeWon,
    cumulativeRevenue,
    cumulativeCosts,
    cumulativeMargin,
    onTimePct
  };
}

// Update simulation at minute t
function updateSimulation(t) {
  state.t = t;
  const scrubber = document.getElementById('timelineScrubber');
  if (scrubber) {
    const span = state.maxT - state.minT;
    const pct = span > 0 ? Math.max(0, Math.min(100, ((t - state.minT) / span) * 100)) : 0;
    scrubber.style.setProperty('--progress', `${pct}%`);
  }
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

  const formatEuros = (cents, signed = false) => {
    const absEuros = Math.abs(cents) / 100;
    const formatted = absEuros.toLocaleString('en-US', {
      minimumFractionDigits: cents % 100 === 0 ? 0 : 2,
      maximumFractionDigits: 2
    });
    if (cents < 0) return `-€${formatted}`;
    if (signed && cents > 0) return `+€${formatted}`;
    return `€${formatted}`;
  };

  const marginCard = document.getElementById('kpiMarginCard');
  const marginEl = document.getElementById('kpiCumulativeMargin');
  const marginIcon = document.getElementById('kpiMarginIcon');
  const marginBreakdown = document.getElementById('kpiMarginBreakdown');
  if (marginEl) {
    marginEl.textContent = formatEuros(metrics.cumulativeMargin, true);
  }
  if (marginBreakdown) {
    marginBreakdown.textContent = `Rev ${formatEuros(metrics.cumulativeRevenue)} − Costs ${formatEuros(metrics.cumulativeCosts)}`;
  }
  if (marginCard && marginEl && marginIcon) {
    const isPositive = metrics.cumulativeMargin > 0;
    const isNegative = metrics.cumulativeMargin < 0;
    marginCard.className = isNegative
      ? 'flex items-center gap-3.5 px-5 py-2.5 rounded-lg bg-surface-container-high border border-error/45 shadow-[0_0_20px_rgba(255,82,82,0.14)] transition-colors'
      : isPositive
        ? 'flex items-center gap-3.5 px-5 py-2.5 rounded-lg bg-surface-container-high border border-secondary/35 shadow-[0_0_20px_rgba(78,222,163,0.12)] transition-colors'
        : 'flex items-center gap-3.5 px-5 py-2.5 rounded-lg bg-surface-container-high border border-white/10 shadow-md transition-colors';
    marginEl.className = isNegative
      ? 'text-2xl font-bold font-mono text-error leading-tight'
      : isPositive
        ? 'text-2xl font-bold font-mono text-secondary leading-tight'
        : 'text-2xl font-bold font-mono text-on-surface leading-tight';
    marginIcon.className = isNegative
      ? 'material-symbols-outlined text-[24px] text-error'
      : isPositive
        ? 'material-symbols-outlined text-[24px] text-secondary'
        : 'material-symbols-outlined text-[24px] text-on-surface';
  }

  const kpiRevenue = document.getElementById('kpiCumulativeRevenue');
  if (kpiRevenue) kpiRevenue.textContent = formatEuros(metrics.cumulativeRevenue);

  const isBelowMin = metrics.onTimePct < 90.0 - 1e-9;
  const onTimeRate = formatRatePct(metrics.onTimePct, isBelowMin);
  const kpiRate = document.getElementById('kpiUtilization');
  const kpiRateIcon = document.getElementById('kpiUtilizationIcon');
  if (kpiRate) {
    kpiRate.textContent = `${onTimeRate}%`;
    kpiRate.className = isBelowMin
      ? 'text-base font-bold font-mono text-error'
      : 'text-base font-bold font-mono text-primary-fixed-dim';
  }
  if (kpiRateIcon) {
    kpiRateIcon.className = isBelowMin
      ? 'material-symbols-outlined text-[20px] text-error'
      : 'material-symbols-outlined text-[20px] text-primary-fixed-dim';
  }
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
function syncLayerToggleButton(btnId, isActive) {
  const btn = document.getElementById(btnId);
  if (!btn) return;
  btn.className = isActive
    ? 'px-2.5 py-1 rounded bg-primary text-surface font-bold flex items-center gap-1 transition-all'
    : 'px-2.5 py-1 rounded bg-surface-container hover:bg-surface-container-high text-on-surface-variant hover:text-on-surface flex items-center gap-1 transition-all border border-white/5';
  btn.setAttribute('aria-pressed', String(isActive));
}

function toggleMapLayer(layer) {
  if (layer === 'vehicles') {
    state.showCars = !state.showCars;
    syncLayerToggleButton('toggleVehiclesBtn', state.showCars);
  } else if (layer === 'labels') {
    state.showLabels = !state.showLabels;
    syncLayerToggleButton('toggleLabelsBtn', state.showLabels);
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

function ingestCsvEntries(entries, sourceLabel = null, serverScore = null) {
  const rawData = {
    places: null,
    roads: null,
    legs: null,
    gigs: null,
    drivers: null,
    requests: null,
    events: null
  };

  let matchedCount = 0;
  for (const [rawName, text] of entries) {
    const name = String(rawName).toLowerCase();
    if (typeof text !== 'string') continue;
    if (name.includes('places.csv')) { rawData.places = parseCSV(text); matchedCount++; }
    else if (name.includes('roads.csv')) { rawData.roads = parseCSV(text); matchedCount++; }
    else if (name.includes('legs.csv')) { rawData.legs = parseCSV(text); matchedCount++; }
    else if (name.includes('gigs.csv')) { rawData.gigs = parseCSV(text); matchedCount++; }
    else if (name.includes('drivers.csv')) { rawData.drivers = parseCSV(text); matchedCount++; }
    else if (name.includes('requests.csv')) { rawData.requests = parseCSV(text); matchedCount++; }
    else if (name.includes('events.csv')) { rawData.events = parseCSV(text); matchedCount++; }
  }

  if (rawData.places || rawData.legs || rawData.events) {
    if (state.isPlaying) {
      togglePlayPause();
    }
    indexDataset(rawData, serverScore);
    const engineBadge = document.getElementById('engineStatusIndicator');
    if (engineBadge && sourceLabel) {
      engineBadge.textContent = sourceLabel;
    }
    return matchedCount;
  }
  return 0;
}

async function processUploadedFiles(files) {
  const entries = [];
  for (const file of files) {
    entries.push([file.name, await file.text()]);
  }
  const firstRel = files[0] && files[0].webkitRelativePath ? files[0].webkitRelativePath.split('/')[0] : null;
  const label = firstRel ? `Dataset Active • ${firstRel}` : `Dataset Simulation Active • ${files.length} Files`;
  const matched = ingestCsvEntries(entries, label);
  if (matched > 0) {
    showNotification(`Dataset loaded successfully from ${files.length} files.`);
  } else {
    alert("Could not recognize dispatch CSV files in the uploaded set. Please include places.csv, legs.csv, events.csv, etc.");
  }
}

function showNotification(msg) {
  const notif = document.getElementById('hudNotification');
  if (notif) {
    notif.innerHTML = `<span class="material-symbols-outlined text-[18px]">check_circle</span><span></span>`;
    const textSpan = notif.querySelector('span:last-child');
    if (textSpan) textSpan.textContent = msg;
    notif.classList.remove('opacity-0', 'pointer-events-none');
    setTimeout(() => {
      notif.classList.add('opacity-0', 'pointer-events-none');
    }, 4500);
  }
}

// ==========================================
// 9. CUSTOM CONSTANTS & SIMULATION RUNNER
// ==========================================
const DEFAULT_CUSTOM_ARGS = {
  PRICE_MULTIPLIER: { '1': 1.2, '2': 1.2, '3': 1.2, '4': 1.5, '5': 1.5, '6': 1.5, '7': 2.0, '8': 1.7 },
  SLACK_BUFFER: { '1': 30, '2': 30, '3': 30, '4': 60, '5': 60, '6': 60, '7': 90, '8': 120 },
  MAX_QUOTE_MULTIPLIER: 1.50,
  PRICE_WAR_MULTIPLIER: 1.15,
  MAX_DEALER_TRUST_PCT: 25,
  TARGET_ONTIME_SHARE: 0.90,
  OPENING_FEE: { '1': 1.0, '2': 1.0, '3': 1.0, '4': 1.0, '5': 1.0, '6': 1.0, '7': 1.4, '8': 1.3 },
  MAX_FEE_MULTIPLE: { '7': 2.0, '8': 2.2 },
  DEFAULT_MAX_FEE_MULTIPLE: 2.50,
  DEADLINE_EARLY_MINUTES: 420,
  QUICK_BOOK_MINUTES: 60,
  STEEP_OPENING_MULTIPLE: 1.50,
  LOW_BATTERY_PERCENT: 30
};

const PER_LEVEL_CUSTOM_KEYS = ['PRICE_MULTIPLIER', 'SLACK_BUFFER', 'OPENING_FEE', 'MAX_FEE_MULTIPLE'];
const INT_CUSTOM_KEYS = new Set(['SLACK_BUFFER', 'MAX_DEALER_TRUST_PCT', 'DEADLINE_EARLY_MINUTES', 'QUICK_BOOK_MINUTES', 'LOW_BATTERY_PERCENT']);
const SELECT_CUSTOM_KEYS = new Set(['DEADLINE_EARLY_MINUTES', 'QUICK_BOOK_MINUTES', 'LOW_BATTERY_PERCENT']);

function cloneCustomArgsDefaults() {
  return JSON.parse(JSON.stringify(DEFAULT_CUSTOM_ARGS));
}

const customArgsState = {
  selectedScope: '8', // '1'..'8' or 'all'
  lastLevel: '8',
  isSimulating: false,
  values: cloneCustomArgsDefaults(),
  uniformValues: {
    PRICE_MULTIPLIER: 1.70,
    SLACK_BUFFER: 120,
    OPENING_FEE: 1.30,
    MAX_FEE_MULTIPLE: 2.20
  }
};

function syncRangeSliderProgress(sliderEl) {
  if (!sliderEl || sliderEl.type !== 'range') return;
  const min = parseFloat(sliderEl.min || '0');
  const max = parseFloat(sliderEl.max || '100');
  const val = parseFloat(sliderEl.value || '0');
  const pct = max > min ? Math.max(0, Math.min(100, ((val - min) / (max - min)) * 100)) : 0;
  sliderEl.style.setProperty('--progress', `${pct}%`);
}

function syncSelectControlValue(selectEl, numericValue, unitFormatter) {
  if (!selectEl) return;
  const strVal = String(numericValue);
  let exists = Array.from(selectEl.options).some(opt => opt.value === strVal);
  if (!exists) {
    const newOpt = document.createElement('option');
    newOpt.value = strVal;
    newOpt.textContent = unitFormatter ? unitFormatter(numericValue) : strVal;
    const inserted = Array.from(selectEl.options).find(opt => Number(opt.value) > Number(numericValue));
    if (inserted) {
      selectEl.insertBefore(newOpt, inserted);
    } else {
      selectEl.appendChild(newOpt);
    }
  }
  selectEl.value = strVal;
}

function getEffectivePerLevelValue(key, levelStr) {
  const map = customArgsState.values[key] || {};
  if (map[levelStr] !== undefined) return map[levelStr];
  if (key === 'MAX_FEE_MULTIPLE') {
    return customArgsState.values.DEFAULT_MAX_FEE_MULTIPLE;
  }
  return DEFAULT_CUSTOM_ARGS[key][levelStr];
}

function formatCustomReadout(key, val) {
  const num = Number(val);
  if (key === 'SLACK_BUFFER') return `${Math.round(num)} min`;
  if (key === 'MAX_DEALER_TRUST_PCT') return `+${Math.round(num)}%`;
  if (key === 'TARGET_ONTIME_SHARE') return `${Math.round(num * 100)}% (${num.toFixed(2)})`;
  return `${num.toFixed(2)}x`;
}

function formatPerLevelSummary(key) {
  if (customArgsState.selectedScope === 'all') {
    const v = customArgsState.uniformValues[key];
    return key === 'SLACK_BUFFER' ? `Uniform L1–8: ${Math.round(v)}m` : `Uniform L1–8: ${Number(v).toFixed(2)}x`;
  }
  const l1 = getEffectivePerLevelValue(key, '1');
  const l4 = getEffectivePerLevelValue(key, '4');
  const l7 = getEffectivePerLevelValue(key, '7');
  const l8 = getEffectivePerLevelValue(key, '8');
  if (key === 'SLACK_BUFFER') {
    return `L1:${l1} L4:${l4} L7:${l7} L8:${l8}m`;
  }
  return `L1:${Number(l1).toFixed(1)} L7:${Number(l7).toFixed(1)} L8:${Number(l8).toFixed(2)}`;
}

function renderCustomArgsForm() {
  const scopeSelect = document.getElementById('customLevelScope');
  if (scopeSelect) scopeSelect.value = customArgsState.selectedScope;

  const badgeText = customArgsState.selectedScope === 'all' ? 'ALL L1–8' : `L${customArgsState.selectedScope}`;
  document.querySelectorAll('[data-scope-badge]').forEach(badge => {
    badge.textContent = badgeText;
  });

  // Update 4 per-level sliders
  for (const key of PER_LEVEL_CUSTOM_KEYS) {
    const val = customArgsState.selectedScope === 'all'
      ? customArgsState.uniformValues[key]
      : getEffectivePerLevelValue(key, customArgsState.selectedScope);
    const ctrl = document.getElementById(`ctrl_${key}`);
    if (ctrl) {
      ctrl.value = String(val);
      syncRangeSliderProgress(ctrl);
    }
    const readout = document.getElementById(`val_${key}`);
    if (readout) readout.textContent = formatCustomReadout(key, val);
    const summary = document.getElementById(`summary_${key}`);
    if (summary) summary.textContent = formatPerLevelSummary(key);
  }

  // Update scalar sliders
  const scalarSliders = [
    'MAX_QUOTE_MULTIPLIER',
    'PRICE_WAR_MULTIPLIER',
    'MAX_DEALER_TRUST_PCT',
    'TARGET_ONTIME_SHARE',
    'DEFAULT_MAX_FEE_MULTIPLE',
    'STEEP_OPENING_MULTIPLE'
  ];
  for (const key of scalarSliders) {
    const val = customArgsState.values[key];
    const ctrl = document.getElementById(`ctrl_${key}`);
    if (ctrl) {
      ctrl.value = String(val);
      syncRangeSliderProgress(ctrl);
    }
    const readout = document.getElementById(`val_${key}`);
    if (readout) readout.textContent = formatCustomReadout(key, val);
  }

  // Update dropdown selects
  syncSelectControlValue(
    document.getElementById('ctrl_DEADLINE_EARLY_MINUTES'),
    customArgsState.values.DEADLINE_EARLY_MINUTES,
    v => `${v} min (${(v / 60).toFixed(1)}h)`
  );
  syncSelectControlValue(
    document.getElementById('ctrl_QUICK_BOOK_MINUTES'),
    customArgsState.values.QUICK_BOOK_MINUTES,
    v => `${v} min`
  );
  syncSelectControlValue(
    document.getElementById('ctrl_LOW_BATTERY_PERCENT'),
    customArgsState.values.LOW_BATTERY_PERCENT,
    v => `${v}% battery`
  );
}

function handleCustomScopeChange(newScope) {
  if (customArgsState.isSimulating) return;
  if (newScope === 'all' && customArgsState.selectedScope !== 'all') {
    const refLvl = customArgsState.selectedScope || '8';
    customArgsState.lastLevel = refLvl;
    for (const key of PER_LEVEL_CUSTOM_KEYS) {
      customArgsState.uniformValues[key] = getEffectivePerLevelValue(key, refLvl);
    }
  } else if (newScope !== 'all') {
    customArgsState.lastLevel = newScope;
  }
  customArgsState.selectedScope = newScope;
  renderCustomArgsForm();
}

function handleCustomControlInput(key, rawVal) {
  if (customArgsState.isSimulating) return;
  const numVal = INT_CUSTOM_KEYS.has(key) ? Math.round(parseFloat(rawVal)) : parseFloat(rawVal);
  if (!Number.isFinite(numVal)) return;

  if (PER_LEVEL_CUSTOM_KEYS.includes(key)) {
    if (customArgsState.selectedScope === 'all') {
      customArgsState.uniformValues[key] = numVal;
      if (key === 'MAX_FEE_MULTIPLE') {
        customArgsState.values.DEFAULT_MAX_FEE_MULTIPLE = numVal;
      }
    } else {
      if (!customArgsState.values[key] || typeof customArgsState.values[key] !== 'object') {
        customArgsState.values[key] = {};
      }
      customArgsState.values[key][customArgsState.selectedScope] = numVal;
    }
  } else {
    customArgsState.values[key] = numVal;
  }

  const ctrl = document.getElementById(`ctrl_${key}`);
  if (ctrl && !SELECT_CUSTOM_KEYS.has(key)) {
    syncRangeSliderProgress(ctrl);
  }
  renderCustomArgsForm();
}

function resetCustomArgsToDefaults() {
  if (customArgsState.isSimulating) return;
  customArgsState.selectedScope = '8';
  customArgsState.lastLevel = '8';
  customArgsState.values = cloneCustomArgsDefaults();
  customArgsState.uniformValues = {
    PRICE_MULTIPLIER: 1.70,
    SLACK_BUFFER: 120,
    OPENING_FEE: 1.30,
    MAX_FEE_MULTIPLE: 2.20
  };
  const levelSelect = document.getElementById('simRunLevel');
  if (levelSelect) levelSelect.value = '8';
  const seedSelect = document.getElementById('simRunSeed');
  if (seedSelect) seedSelect.value = 'random';
  renderCustomArgsForm();
  setCustomArgsStatus('Defaults restored — click "Save & Run Simulation" to apply.', 'info');
}

function setCustomArgsStatus(message, tone = 'neutral') {
  const statusEl = document.getElementById('customArgsStatus');
  if (!statusEl) return;
  let icon = 'description';
  let colorClass = 'text-on-surface-variant';
  if (tone === 'success') {
    icon = 'check_circle';
    colorClass = 'text-secondary';
  } else if (tone === 'error') {
    icon = 'error';
    colorClass = 'text-error';
  } else if (tone === 'info') {
    icon = 'info';
    colorClass = 'text-primary';
  }
  statusEl.className = `text-xs font-mono ${colorClass} flex items-center gap-1.5`;
  statusEl.innerHTML = `<span class="material-symbols-outlined text-[16px]">${icon}</span><span></span>`;
  const textSpan = statusEl.querySelector('span:last-child');
  if (textSpan) textSpan.textContent = message;
}

function setCustomFormBusy(isBusy, level = 8) {
  customArgsState.isSimulating = isBusy;
  const form = document.getElementById('customArgsForm');
  if (form) {
    form.querySelectorAll('input, select, button').forEach(el => {
      el.disabled = isBusy;
    });
  }
  const saveIcon = document.getElementById('saveCustomArgsIcon');
  const saveLabel = document.getElementById('saveCustomArgsLabel');
  if (saveIcon) {
    saveIcon.textContent = isBusy ? 'progress_activity' : 'rocket_launch';
    saveIcon.classList.toggle('animate-spin', isBusy);
  }
  if (saveLabel) {
    saveLabel.textContent = isBusy ? `Running Level ${level}...` : 'Save & Run Simulation';
  }
}

function updateSimProgressPanel({ visible, phaseText, worldId, elapsedSeconds, logs, isError = false }) {
  const panel = document.getElementById('customSimProgressPanel');
  if (!panel) return;
  if (!visible) {
    panel.classList.add('hidden');
    return;
  }
  panel.classList.remove('hidden');

  const spinner = document.getElementById('customSimSpinner');
  const phaseEl = document.getElementById('customSimPhaseLabel');
  const worldBadge = document.getElementById('customSimWorldBadge');
  const elapsedEl = document.getElementById('customSimElapsed');
  const logsEl = document.getElementById('customSimLogs');

  if (spinner) {
    spinner.textContent = isError ? 'error' : 'progress_activity';
    spinner.className = isError
      ? 'material-symbols-outlined text-[16px] text-error'
      : 'material-symbols-outlined text-[16px] animate-spin text-secondary';
  }
  if (phaseEl) {
    phaseEl.textContent = phaseText || '';
    phaseEl.className = isError ? 'text-error' : 'text-secondary';
  }
  if (worldBadge) {
    if (worldId) {
      worldBadge.textContent = worldId;
      worldBadge.classList.remove('hidden');
    } else {
      worldBadge.classList.add('hidden');
    }
  }
  if (elapsedEl && typeof elapsedSeconds === 'number') {
    elapsedEl.textContent = `${elapsedSeconds.toFixed(1)}s`;
  }
  if (logsEl && Array.isArray(logs)) {
    logsEl.textContent = logs.join('\n');
    logsEl.scrollTop = logsEl.scrollHeight;
  }
}

async function callBackendApi(routePath, method = 'GET', payload = null) {
  const cleanRoute = routePath.replace(/^\/+/, '');
  const candidates = [];
  if (window.location.protocol === 'http:' || window.location.protocol === 'https:') {
    candidates.push(cleanRoute);
  }
  if (window.location.origin !== 'http://127.0.0.1:8000' && window.location.origin !== 'http://localhost:8000') {
    candidates.push(`http://127.0.0.1:8000/${cleanRoute}`);
  }

  let lastError = null;
  for (const url of candidates) {
    try {
      const options = {
        method,
        headers: payload ? { 'Content-Type': 'application/json' } : {}
      };
      if (payload) {
        options.body = JSON.stringify(payload);
      }
      const res = await fetch(url, options);
      const contentType = res.headers.get('content-type') || '';
      if (!contentType.includes('application/json')) {
        lastError = new Error(`Static server at ${url} does not expose /${cleanRoute}.`);
        continue;
      }
      const data = await res.json();
      if (!res.ok || data.ok === false) {
        const err = new Error(data.error || `Server returned HTTP ${res.status}`);
        err.status = res.status;
        err.data = data;
        throw err;
      }
      return data;
    } catch (err) {
      lastError = err;
      if (err && err.status && err.status !== 404) {
        throw err;
      }
    }
  }
  throw lastError || new Error('API server unreachable');
}

function applyServerCustomArgs(data) {
  if (!data || !data.values) return;
  const v = data.values;
  customArgsState.values = cloneCustomArgsDefaults();

  for (const [key, val] of Object.entries(v)) {
    if (PER_LEVEL_CUSTOM_KEYS.includes(key) && val && typeof val === 'object') {
      customArgsState.values[key] = { ...val };
    } else if (val !== undefined) {
      customArgsState.values[key] = val;
    }
  }

  for (const key of PER_LEVEL_CUSTOM_KEYS) {
    customArgsState.uniformValues[key] = getEffectivePerLevelValue(key, '8');
  }

  if (data.scope === 'uniform') {
    customArgsState.selectedScope = 'all';
  } else if (customArgsState.selectedScope === 'all') {
    customArgsState.selectedScope = customArgsState.lastLevel || '8';
  }

  renderCustomArgsForm();
}

async function openCustomArgsModal() {
  const modal = document.getElementById('customArgsModal');
  if (!modal) return;
  modal.classList.remove('hidden');
  if (customArgsState.isSimulating) return;

  updateSimProgressPanel({ visible: false });
  renderCustomArgsForm();
  setCustomArgsStatus('Reading custom_args.py...', 'info');

  try {
    const data = await callBackendApi('api/custom-args', 'GET');
    applyServerCustomArgs(data);
    setCustomArgsStatus('Synced with custom_args.py — ready to run simulation', 'success');
  } catch (_err) {
    setCustomArgsStatus('Run "python3 website/server.py" to enable custom simulations', 'error');
  }
}

function closeCustomArgsModal() {
  if (customArgsState.isSimulating) return;
  const modal = document.getElementById('customArgsModal');
  if (modal) modal.classList.add('hidden');
}

function sleepMs(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function submitCustomArgsForm(event) {
  event.preventDefault();
  if (customArgsState.isSimulating) return;

  const levelSelect = document.getElementById('simRunLevel');
  const runLevel = levelSelect ? parseInt(levelSelect.value, 10) || 8 : 8;
  const seedSelect = document.getElementById('simRunSeed');
  const runSeed = seedSelect ? seedSelect.value : 'random';
  const seedFlagText = runSeed && runSeed !== 'random' ? ` --seed ${runSeed}` : '';
  const isUniform = customArgsState.selectedScope === 'all';
  const payloadValues = {};

  for (const [key, val] of Object.entries(customArgsState.values)) {
    if (PER_LEVEL_CUSTOM_KEYS.includes(key)) {
      payloadValues[key] = isUniform ? customArgsState.uniformValues[key] : { ...val };
    } else {
      payloadValues[key] = val;
    }
  }

  setCustomFormBusy(true, runLevel);
  updateSimProgressPanel({
    visible: true,
    phaseText: `Writing custom_args.py & starting dispatch.py --level ${runLevel}${seedFlagText} --custom...`,
    worldId: null,
    elapsedSeconds: 0,
    logs: [`Preparing simulation (level ${runLevel}, seed: ${runSeed})...`],
    isError: false
  });
  setCustomArgsStatus('Simulation in progress — waiting for dispatch.py to finish...', 'info');

  try {
    const startResp = await callBackendApi('api/simulate', 'POST', {
      level: runLevel,
      seed: runSeed,
      scope: isUniform ? 'uniform' : 'per_level',
      values: payloadValues
    });

    if (startResp.customArgs) {
      applyServerCustomArgs(startResp.customArgs);
    }

    const jobId = startResp.jobId;
    while (true) {
      await sleepMs(800);
      let statusResp;
      try {
        statusResp = await callBackendApi(`api/simulate/status?jobId=${encodeURIComponent(jobId)}`, 'GET');
      } catch (pollErr) {
        if (pollErr && pollErr.data && pollErr.data.status === 'error') {
          statusResp = pollErr.data;
        } else {
          throw pollErr;
        }
      }

      if (statusResp.status === 'error') {
        const errLogs = statusResp.logs && statusResp.logs.length > 0
          ? statusResp.logs
          : [statusResp.error || 'Simulation failed.'];
        updateSimProgressPanel({
          visible: true,
          phaseText: `Simulation failed: ${statusResp.error || 'Unknown error'}`,
          worldId: statusResp.worldId,
          elapsedSeconds: statusResp.elapsedSeconds || 0,
          logs: errLogs,
          isError: true
        });
        setCustomArgsStatus(statusResp.error || 'Simulation failed.', 'error');
        return;
      }

      const phaseLabel = statusResp.phase === 'generating_csv'
        ? `Generating CSV dataset (${statusResp.folder || 'spread-reveal-csv'})...`
        : `Running python3 dispatch.py --level ${runLevel}${seedFlagText} --custom...`;

      updateSimProgressPanel({
        visible: true,
        phaseText: phaseLabel,
        worldId: statusResp.worldId,
        elapsedSeconds: statusResp.elapsedSeconds || 0,
        logs: statusResp.logs || [],
        isError: false
      });

      if (statusResp.status === 'done') {
        break;
      }
    }

    updateSimProgressPanel({
      visible: true,
      phaseText: 'Loading generated CSV folder into Mission Control...',
      worldId: null,
      elapsedSeconds: undefined,
      logs: undefined,
      isError: false
    });

    const resultResp = await callBackendApi(`api/simulate/result?jobId=${encodeURIComponent(jobId)}`, 'GET');
    const fileEntries = Object.entries(resultResp.files || {});
    const seedLabel = resultResp.seed ? `, Seed ${resultResp.seed}` : '';
    const datasetLabel = `Custom Sim • ${resultResp.folder} (Level ${resultResp.level}${seedLabel})`;
    const matched = ingestCsvEntries(fileEntries, datasetLabel, resultResp.score || null);
    if (matched === 0) {
      throw new Error('Simulation finished, but no valid CSV tables were returned.');
    }

    setCustomFormBusy(false, runLevel);
    updateSimProgressPanel({ visible: false });
    setCustomArgsStatus(`Loaded ${resultResp.folder} successfully`, 'success');
    closeCustomArgsModal();

    const summaryToast = resultResp.summaryLine
      ? `Loaded ${resultResp.folder} • ${resultResp.summaryLine.replace(/^End\s+/, '')}`
      : `Simulation complete! Loaded ${resultResp.folder} (Level ${resultResp.level}).`;
    showNotification(summaryToast);
  } catch (err) {
    const msg = (err && err.status === 404)
      ? 'Restart "python3 website/server.py" in your terminal so /api/simulate is active.'
      : (err && err.message ? err.message : 'Could not run simulation. Ensure python3 website/server.py is running.');
    updateSimProgressPanel({
      visible: true,
      phaseText: msg,
      worldId: null,
      elapsedSeconds: 0,
      logs: [msg],
      isError: true
    });
    setCustomArgsStatus(msg, 'error');
    console.warn('Simulation error:', err);
  } finally {
    setCustomFormBusy(false, runLevel);
  }
}

// ==========================================
// 10. INITIALIZATION
// ==========================================
document.addEventListener('DOMContentLoaded', () => {
  setupFileDrop();

  // Close modals on Escape key (except while simulation is running)
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      if (!customArgsState.isSimulating) {
        closeCustomArgsModal();
      }
      const dropzone = document.getElementById('dropzoneModal');
      if (dropzone) dropzone.classList.add('hidden');
    }
  });

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
