# In Layers Core

Python port of the Node-in-Layers core framework.
Supports  Domains, config and layers loading, and cross-layer logging.

Key points:
- Domains explicitly provided in config (no convention discovery)
- Layers are loaded in configured order (supports composite layers)
- Cross-layer logging with automatic id propagation and function wraps


## Contributing

### Running Unit Tests
```bash
poetry run pytest --cov=. --cov-report=term-missing --cov-report=html -q
```

### Auto-Cleaning / Checking Tools
```bash
./bin/lint.sh
```

### Publishing
```bash
./bin/deploy.sh
```
