# SIH_Fleet_Priority — SIH26123

**Edge-AI Based Distributed Fleet Coordination for Autonomous Mobile Robots (AMRs) in Smart Warehouses**

A multi-robot warehouse simulation, peer-to-peer coordination protocol, and benchmark harness for decentralized AMR priority and path-conflict resolution.

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m pytest -q
python backend/server.py                       # http://127.0.0.1:8000
```

**Live Deployment**: https://bios-sih26123.azurewebsites.net/  
**Local Dashboard**: http://127.0.0.1:8000  
**Documentation**: http://127.0.0.1:8000/docs

## Key Features

- **Zero third-party dependencies** for simulation core and benchmark (stdlib only)
- **Decentralized coordination** with BIOS_PIBT.6 and Auction V2
- **Real edge processes** with authenticated UDP multicast
- **3D digital twin** dashboard with interactive camera modes
- **Comprehensive benchmarking** with statistical evidence

## Results

✅ **90/90 acceptance runs passed** (4, 6, 8 robot fleets)  
✅ **0 observed collisions** across 88.39 robot-hours  
✅ **65.22% / 50.63% / 33.46%** minimum task-time reduction vs stop-and-wait

See [Benchmark and Evidence](http://127.0.0.1:8000/docs#benchmark) for details.

## Additional Commands

```bash
python edge_demo.py --robots 3 --duration 5    # real processes + signed UDP
python fault_campaign.py --seeds 30 --jobs 8   # loss, partition, crash recovery
python benchmark.py --seeds 30 --jobs 8        # strict SIH acceptance gate
python auction_v2_campaign.py --seeds 30 --jobs 8  # Auction V2 release gate
```

## Documentation

Complete documentation is available at **/docs** when running the server:

- Problem Statement & Requirements
- Architecture & Protocol Design
- Benchmark Results & Evidence
- Deployment Guide
- API Reference
- Testing & Validation

**Team**: Bharat Electronics Limited · Software · Robotics and Drones  
**Problem Statement**: PS26123
