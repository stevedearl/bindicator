Bindicator frontend (React + Vite + Tailwind)
=============================================

Local dev
---------

1) Install dependencies

   cd frontend
   npm install

2) Start the dev server

   npm start

Your app is available at http://127.0.0.1:5173

Notes
-----
- A Vite dev proxy forwards /api/* requests to http://127.0.0.1:8000
- Ensure the FastAPI server is running on port 8000.
- TailwindCSS is preconfigured via postcss.

RBWM flow in the UI
-------------------
- Enter postcode and house number → frontend calls `/api/resolve` to find your UPRN.
- If a confident match exists, it auto-selects; otherwise you can choose from a shortlist.
- Bins are fetched using `/api/bins?uprn=...`. Use the card’s Refresh to bypass cache.

Build and deploy
----------------
- Build: `npm run build` → output in `frontend/dist`
- GitHub Pages: set env `VITE_BASE=/bindicator/` during build if deploying under a subpath.
  Example PowerShell:
    `$env:VITE_BASE='/bindicator/'; npm run build`
  Then publish `frontend/dist` to Pages.
