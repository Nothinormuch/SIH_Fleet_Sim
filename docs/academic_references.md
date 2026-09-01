# 📚 Academic Research References & Algorithmic Foundations (SIH26123)

This document lists all the formal algorithms, standard methods, and academic research papers implemented or referenced in the **SIH_Fleet_Sim** codebase.

---

## 1. Multi-Agent Traffic & Conflict Resolution

### 🔹 **PIBT (Priority Inheritance with Backtracking)**
* **Seminal Paper:** 
  > Okumura, K., Machida, M., Défago, X., & Tamura, Y. (2022). *"Priority Inheritance with Backtracking for Multi-Agent Path Finding on Graphs."* **Artificial Intelligence**, 310, 103752. (Earlier version in IJCAI 2019).
* **Where we use it:** Core traffic solver in `src/amr.py` (`BIOS_PIBT.5`). Resolves instantaneous node conflicts by recursively inheriting priority to push lower-priority robots into passing bays.
* **PPT Citation:** `Okumura et al., "Priority Inheritance with Backtracking for MAPF", Artificial Intelligence (2022)`

---

### 🔹 **Space-Time A\* (4D Cooperative Pathfinding)**
* **Seminal Paper:**
  > Silver, D. (2005). *"Cooperative Pathfinding."* **Proceedings of the AAAI Conference on Artificial Intelligence and Interactive Digital Entertainment (AIIDE)**, 1(1), 117-122.
* **Where we use it:** Path planner for individual AMRs searching across $(x, y, \theta, t)$ grid with reservation tables in `src/amr.py` and central manager.
* **PPT Citation:** `Silver, D., "Cooperative Pathfinding", AIIDE (2005)`

---

### 🔹 **CBS (Conflict-Based Search for Multi-Agent Pathfinding)**
* **Seminal Paper:**
  > Sharon, G., Stern, R., Felner, A., & Sturtevant, N. R. (2015). *"Conflict-Based Search for Optimal Multi-Agent Pathfinding."* **Artificial Intelligence**, 219, 40-66.
* **Where we use it:** Referenced as the theoretical optimal benchmark for discrete multi-agent path planning in `docs/CRITIQUE.md`.
* **PPT Citation:** `Sharon et al., "Conflict-Based Search for Optimal MAPF", Artificial Intelligence (2015)`

---

## 2. Task Allocation & Combinatorial Optimization

### 🔹 **Market-Based Multi-Robot Auctions (Sequential Single-Item / SSI)**
* **Seminal Paper:**
  > Koenig, S., Tovey, C., Lagoudakis, M., et al. (2006). *"The Sequential Single-Item Auction Approach to Distributed Multi-Robot Task Allocation."* In **Decision Making in Manufacturing and Services**, 1(1-2).  
  > Dias, M. B., Zlot, R., Kalra, N., & Stentz, A. (2006). *"Market-Based Multirobot Coordination: A Survey and Analysis."* **Proceedings of the IEEE**, 94(7), 1257-1270.
* **Where we use it:** Decentralized task bidding (`src/task_allocation.py`) where robots compute marginal cost bids $C_i = f(\text{Battery}, \text{Distance}, \text{Payload})$.
* **PPT Citation:** `Dias et al., "Market-Based Multirobot Coordination", IEEE Proceedings (2006)`

---

### 🔹 **The Hungarian Algorithm (Kuhn-Munkres Bipartite Matching)**
* **Seminal Paper:**
  > Kuhn, H. W. (1955). *"The Hungarian Method for the Assignment Problem."* **Naval Research Logistics Quarterly**, 2(1‐2), 83-97.  
  > Munkres, J. (1957). *"Algorithms for the Assignment and Transportation Problems."* **Journal of the SIAM**, 5(1), 32-38.
* **Where we use it:** Centralized optimal assignment baseline implemented in `src/task_allocation.py` ($O(N^3)$ matrix solver).
* **PPT Citation:** `Kuhn, H. W., "The Hungarian Method for the Assignment Problem", NRLQ (1955)`

---

## 3. Distributed Systems Theory & Networking

### 🔹 **Fischer-Lynch-Paterson (FLP) Impossibility Theorem**
* **Seminal Paper:**
  > Fischer, M. J., Lynch, N. A., & Paterson, M. S. (1985). *"Impossibility of Distributed Consensus with One Faulty Process."* **Journal of the ACM (JACM)**, 32(2), 374-382.
* **Where we use it:** Core theoretical defense in `docs/CRITIQUE.md` proving why no lossy wireless protocol can guarantee consensus, justifying why **Safety (50 Hz local loop)** must be strictly separated from **Networking**.
* **PPT Citation:** `Fischer, Lynch, & Paterson, "Impossibility of Distributed Consensus", JACM (1985)`

---

### 🔹 **HMAC: Keyed-Hashing for Message Authentication**
* **Specification:**
  > Krawczyk, H., Bellare, M., & Canetti, R. (1997). *"HMAC: Keyed-Hashing for Message Authentication."* **IETF RFC 2104**.
* **Where we use it:** Message integrity verification and anti-spoofing in `src/transport.py` using HMAC-SHA256 signatures on wire UDP frames.
* **PPT Citation:** `IETF RFC 2104 / FIPS PUB 198-1 (HMAC-SHA256)`

---

## 4. Machine Learning & Neuroevolution

### 🔹 **Evolution Strategies (ES) for Policy Optimization**
* **Seminal Paper:**
  > Salimans, T., Ho, J., Chen, X., Sidor, S., & Sutskever, I. (2017). *"Evolution Strategies as a Scalable Alternative to Reinforcement Learning."* **arXiv:1703.03864 (OpenAI)**.
* **Where we use it:** Training the compact 549-parameter neural policy network in `src/evolve.py` and `src/bios4.py` without requiring PyTorch/GPU runtimes.
* **PPT Citation:** `Salimans et al., "Evolution Strategies as a Scalable Alternative to RL", OpenAI (2017)`

---

## 5. Industrial Robotics & Safety Standards

### 🔹 **ISO 3691-4:2020 & EN ISO 13849-1**
* **Standard Specification:**
  > International Organization for Standardization. (2020). *"Industrial trucks — Safety requirements and verification — Part 4: Driverless industrial trucks and their systems"* (**ISO 3691-4:2020**).
* **Where we use it:** Mathematical sizing of dynamic braking safety field $D_{\text{stop}} = \frac{v^2}{2a} + v\tau + \text{margin}$ and 50 Hz protective e-stop loop in `src/amr.py`.
* **PPT Citation:** `ISO 3691-4:2020 (Safety Requirements for Driverless Industrial Trucks / AMRs)`
