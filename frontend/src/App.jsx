import React, { useEffect, useMemo, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'

function BinPill({ color, label }) {
  const src = {
    blue: '/bins/blue.svg',
    green: '/bins/green.svg',
    black: '/bins/black.svg'
  }[color]
  return (
    <div className="pill" title={`${label} bin`} aria-label={`${label} bin`}>
      <img src={src} alt="" className="h-6 w-6" />
      <span className="capitalize">{label}</span>
    </div>
  )
}

function BinImage({ color, className }) {
  const [src, setSrc] = useState(`/bins/${color}.png`)
  // If a PNG isn’t present, fall back to our SVG
  return (
    <img
      src={src}
      onError={() => setSrc(`/bins/${color}.svg`) }
      alt=""
      className={`bin-img ${className || ''}`}
    />
  )
}

function BinFigure({ color, label, delay = 0 }) {
  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.9, y: 8 }}
      animate={{ opacity: 1, scale: 1, y: 0 }}
      transition={{ type: 'spring', stiffness: 280, damping: 22, delay }}
      className="flex w-28 flex-col items-center"
    >
      <div className="bin-plate h-24 w-24">
        <BinImage color={color} className="h-20 w-20 object-contain" />
      </div>
      <div className="mt-2 text-sm font-medium capitalize text-gray-700 dark:text-gray-200">{label}</div>
    </motion.div>
  )
}

export default function App() {
  const [postcode, setPostcode] = useState('SL6 6AH')
  const [house, setHouse] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [data, setData] = useState(null)
  const [candidates, setCandidates] = useState([])
  const [selected, setSelected] = useState(null) // { uprn, address }
  const [stage, setStage] = useState('form') // 'form' | 'choose' | 'result'
  const [showSettings, setShowSettings] = useState(false)
  const [alwaysRefresh, setAlwaysRefresh] = useState(() => localStorage.getItem('bindicator.alwaysRefresh') === '1')
  const [dark, setDark] = useState(() => localStorage.getItem('bindicator.dark') === '1')

  const binLabels = useMemo(() => ({
    blue: 'Recycling',
    green: 'Garden',
    black: 'Rubbish'
  }), [])

  // Extract a sensible house token from a full address line.
  // Works for numbers (e.g., "22 The Crescent") and names (e.g., "The Cottage, ...").
  function extractHouseFromAddress(addr) {
    if (!addr || typeof addr !== 'string') return ''
    const firstSeg = addr.split(',')[0].trim()
    if (!firstSeg) return ''
    // If it starts with a number (optionally with a letter suffix like 22A), return just that token
    const numMatch = firstSeg.match(/^\s*(\d+[A-Za-z]?)/)
    if (numMatch) return numMatch[1]
    // Otherwise, try to isolate a house name before a common street type
    const streetTypes = [
      'Close','Road','Street','Avenue','Court','Lane','Drive','Way','Gardens','Place','Crescent','Rise','Hill','Green','Grove','Park','Vale','Row','Terrace','Mews','Square','Walk','View'
    ]
    for (const t of streetTypes) {
      const idx = firstSeg.toLowerCase().indexOf(' ' + t.toLowerCase())
      if (idx > 0) return firstSeg.slice(0, idx).trim()
    }
    // Fallback: return the whole first segment (e.g., a pure house name)
    return firstSeg
  }

  function formatDate(iso) {
    try {
      const d = new Date(iso + 'T00:00:00')
      return d.toLocaleDateString(undefined, { weekday: 'short', day: '2-digit', month: 'short', year: 'numeric' })
    } catch { return iso }
  }

  function formatTime(iso) {
    try {
      const d = new Date(iso)
      return d.toLocaleString()
    } catch { return iso }
  }

  useEffect(() => {
    const saved = localStorage.getItem('bindicator.selection')
    if (saved) {
      try {
        const s = JSON.parse(saved)
        if (s?.uprn && s?.address) {
          setSelected(s)
          setStage('result')
          const ar = localStorage.getItem('bindicator.alwaysRefresh') === '1'
          fetchBinsByUPRN(s.uprn, { refresh: ar })
          // Populate the house field so the chosen address context remains visible
          setHouse((prev) => prev || extractHouseFromAddress(s.address))
        }
      } catch {}
    }
  }, [])

  useEffect(() => {
    localStorage.setItem('bindicator.alwaysRefresh', alwaysRefresh ? '1' : '0')
  }, [alwaysRefresh])

  useEffect(() => {
    const root = document.documentElement
    if (dark) root.classList.add('dark')
    else root.classList.remove('dark')
    localStorage.setItem('bindicator.dark', dark ? '1' : '0')
  }, [dark])

  async function onResolve(e) {
    e.preventDefault()
    setError('')
    setLoading(true)
    setData(null)
    setCandidates([])
    try {
      const url = `/api/resolve?postcode=${encodeURIComponent(postcode)}&house=${encodeURIComponent(house)}`
      const res = await fetch(url)
      if (!res.ok) {
        let msg = `Request failed: ${res.status}`
        try {
          const err = await res.json()
          msg = err.hint ? `${err.error}. ${err.hint}` : (err.error || msg)
        } catch {}
        throw new Error(msg)
      }
      const list = await res.json()
      if (Array.isArray(list) && list.length > 0) {
        // If a clear top exact match, choose it; otherwise let user pick
        const top = list[0]
        const exactTop = top?.exact && (list.length === 1 || top.score >= (list[1]?.score ?? 0))
        if (exactTop) {
          choose({ uprn: top.uprn, address: top.address })
        } else {
          setCandidates(list)
          setStage('choose')
        }
      } else {
        // Fallback to postcode-only
        await fetchBinsByPostcode(postcode)
      }
    } catch (err) {
      setError(err.message || 'Failed to resolve address')
    } finally {
      setLoading(false)
    }
  }

  function choose(item) {
    setSelected(item)
    localStorage.setItem('bindicator.selection', JSON.stringify(item))
    fetchBinsByUPRN(item.uprn, { refresh: alwaysRefresh })
    const token = extractHouseFromAddress(item.address)
    if (token) setHouse(token)
  }

  function clearSelection() {
    setSelected(null)
    localStorage.removeItem('bindicator.selection')
    setData(null)
    setStage('form')
  }

  async function fetchBinsByUPRN(uprn, opts = {}) {
    setError('')
    setLoading(true)
    setData(null)
    try {
      const qp = new URLSearchParams()
      qp.set('uprn', uprn)
      if (opts.refresh) qp.set('refresh', 'true')
      qp.set('t', Date.now().toString())
      const url = `/api/bins?${qp}`
      const res = await fetch(url)
      if (!res.ok) {
        let msg = `Request failed: ${res.status}`
        try {
          const err = await res.json()
          msg = err.hint ? `${err.error}. ${err.hint}` : (err.error || msg)
        } catch {}
        throw new Error(msg)
      }
      const json = await res.json()
      setData(json)
      setStage('result')
    } catch (err) {
      setError(err.message || 'Failed to fetch bin info')
    } finally {
      setLoading(false)
    }
  }

  async function fetchBinsByPostcode(pc) {
    setError('')
    setLoading(true)
    setData(null)
    try {
      const url = `/api/bins?postcode=${encodeURIComponent(pc)}`
      const res = await fetch(url)
      if (!res.ok) {
        let msg = `Request failed: ${res.status}`
        try {
          const err = await res.json()
          msg = err.hint ? `${err.error}. ${err.hint}` : (err.error || msg)
        } catch {}
        throw new Error(msg)
      }
      const json = await res.json()
      setData(json)
      setStage('result')
    } catch (err) {
      setError(err.message || 'Failed to fetch bin info')
    } finally {
      setLoading(false)
    }
  }

  async function loadAllAddresses() {
    setError('')
    setLoading(true)
    try {
      const res = await fetch(`/api/addresses?postcode=${encodeURIComponent(postcode)}`)
      if (!res.ok) throw new Error(`Request failed: ${res.status}`)
      const list = await res.json()
      setCandidates(Array.isArray(list) ? list : [])
      setStage('choose')
    } catch (err) {
      setError(err.message || 'Failed to load full address list')
    } finally {
      setLoading(false)
    }
  }

  const tips = [
    'Squash plastic bottles to save space.',
    'Rinse food containers before recycling.',
    'Flatten cardboard to fit more in.',
    'Garden waste makes great compost.',
    'Electricals are collected on recycling week.'
  ]
  const [tip, setTip] = useState('')
  useEffect(() => {
    const today = new Date()
    const key = `${today.getUTCFullYear()}-${today.getUTCMonth()+1}-${today.getUTCDate()}`
    let h = 0
    for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) >>> 0
    const idx = h % tips.length
    setTip(tips[idx])
  }, [])

  function getMessage(bins) {
    if (!bins) return ''
    const hasGreen = bins.includes('green')
    const hasBlack = bins.includes('black')
    if (hasGreen) return "Garden waste time 🌿"
    if (hasBlack) return "Black bin week — declutter day 🗑️"
    return "It’s a recycling week! Let’s get sorting ♻️"
  }

  return (
    <div className="mx-auto max-w-3xl p-6">
      <header className="mb-6 flex items-center justify-center gap-3">
        <img src="/bins/mascot.svg" alt="Bindicator mascot" className="h-10 w-10 animate-bounce" />
        <div className="text-center">
          <h1 className="text-3xl font-semibold tracking-tight">Bindicator</h1>
          <p className="text-sm text-gray-600 dark:text-gray-300">Helping Maidenhead stay clean and green</p>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <button
            type="button"
            aria-label="Toggle dark mode"
            onClick={() => setDark((d) => !d)}
            className="rounded-lg bg-gray-900/80 px-3 py-1.5 text-xs font-medium text-white shadow hover:bg-black dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white"
          >
            {dark ? 'Light' : 'Dark'}
          </button>
          <button
            type="button"
            onClick={() => setShowSettings(true)}
            className="rounded-lg bg-gray-200 px-3 py-1.5 text-xs font-medium text-gray-700 shadow hover:bg-gray-300 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700"
            aria-haspopup="dialog"
          >
            Settings
          </button>
        </div>
      </header>

      <form onSubmit={onResolve} className="search-bar mx-auto flex flex-col gap-2 p-3 sm:flex-row sm:items-center">
        <input
          aria-label="Postcode"
          value={postcode}
          onChange={(e) => setPostcode(e.target.value)}
          className="flex-1 rounded-xl border border-gray-300 bg-white px-4 py-3 text-base shadow-sm focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-200 text-gray-900 placeholder-gray-400 dark:text-gray-900 dark:placeholder-gray-500 caret-blue-600"
          placeholder="Postcode (e.g. SL6 6AH)"
        />
        <input
          aria-label="House number"
          value={house}
          onChange={(e) => setHouse(e.target.value)}
          className="flex-1 rounded-xl border border-gray-300 bg-white px-4 py-3 text-base shadow-sm focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-200 text-gray-900 placeholder-gray-400 dark:text-gray-900 dark:placeholder-gray-500 caret-blue-600"
          placeholder="House no./name (e.g. 22)"
        />
        <button
          type="submit"
          className="rounded-xl bg-blue-600 px-5 py-3 text-white shadow-lg hover:bg-blue-700 active:bg-blue-800"
        >
          Find address
        </button>
      </form>

      {loading && (
        <div className="mt-4 flex items-center justify-center text-sm text-gray-600">
          <div className="loading-dots">
            <span>•</span>
            <span>•</span>
            <span>•</span>
          </div>
        </div>
      )}

      {error && (
        <div className="mt-4 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>
      )}

      {stage === 'choose' && candidates?.length > 0 && (
        <div className="card mt-6 p-4">
          <div className="mb-3 text-sm text-gray-600">Select your address</div>
          <ul className="max-h-80 overflow-auto divide-y divide-gray-100">
            {candidates.map((it) => (
              <li key={it.uprn} className="flex items-center justify-between gap-2 py-2">
                <div className="text-sm">{it.address}</div>
                <button onClick={() => choose({uprn: it.uprn, address: it.address})} className="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-medium text-white shadow hover:bg-blue-700">
                  Select
                </button>
              </li>
            ))}
          </ul>
          <div className="mt-3 text-xs text-gray-500">Can't see your address? <button onClick={loadAllAddresses} className="underline">Show full list</button></div>
        </div>
      )}

      {data && stage === 'result' && (
        <motion.div className="card mt-6 p-5" initial={{opacity:0, y:12}} animate={{opacity:1, y:0}} transition={{type:'spring', stiffness:260, damping:24}}>
          {selected ? (
            <>
              <div className="mb-2 text-sm text-gray-500">Address</div>
              <div className="mb-1 font-medium">{selected.address}</div>
              <div className="mb-4 text-xs text-gray-400">UPRN {selected.uprn} · <button onClick={clearSelection} className="underline">Change address</button></div>
            </>
          ) : (
            <>
              <div className="mb-2 text-sm text-gray-500">Postcode</div>
              <div className="mb-4 font-medium">{data.postcode}</div>
            </>
          )}

          <div className="mb-2 flex items-center justify-between text-sm text-gray-500">
            <span>Next collection</span>
            {selected && (
              <button onClick={() => fetchBinsByUPRN(selected.uprn, { refresh: true })} className="rounded-lg bg-gray-800 px-3 py-1 text-xs font-medium text-white hover:bg-black">Refresh</button>
            )}
          </div>
          <div className="mb-1 text-xl font-semibold tracking-tight">{formatDate(data.nextCollectionDate)}</div>
          <div className="mb-4 text-sm text-emerald-700">{getMessage(data.bins)}</div>

          <div className="mb-2 text-sm text-gray-500">Bins</div>
          <div className="flex flex-wrap items-start justify-start gap-5 sm:gap-7">
            {data.bins?.map((b, i) => (
              <BinFigure key={b} color={b} label={binLabels[b] || b} delay={i * 0.06} />
            ))}
          </div>

          <div className="mt-4 text-xs text-gray-400">Source: {data.source} · Updated {formatTime(data.fetchedAt)}</div>
        </motion.div>
      )}

      <AnimatePresence>
        <motion.div key={tip} initial={{opacity:0, y:8}} animate={{opacity:1, y:0}} exit={{opacity:0, y:-8}} transition={{duration:.25}} className="card mt-6 flex items-center gap-3 p-4 text-sm text-gray-700 dark:text-gray-200">
          <div className="bin-plate h-10 w-10 text-xl">💡</div>
          <div>
            <div className="text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400">Tip of the week</div>
            <div className="text-sm sm:text-base">{tip}</div>
          </div>
        </motion.div>
      </AnimatePresence>

      <footer className="mt-8 text-center text-xs text-gray-400">
        Built for neighbours in RBWM · Live data when available
      </footer>

      <AnimatePresence>
        {showSettings && (
          <motion.div role="dialog" aria-modal="true" className="fixed inset-0 z-50 flex items-end sm:items-center justify-center"
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
          >
            <div className="absolute inset-0 bg-black/40" onClick={() => setShowSettings(false)}></div>
            <motion.div className="card relative m-4 w-full max-w-md p-5" initial={{ y: 24, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ y: 24, opacity: 0 }}>
              <div className="mb-3 text-lg font-semibold">Settings</div>
              <label className="mb-3 flex items-center gap-2 text-sm">
                <input type="checkbox" checked={alwaysRefresh} onChange={(e) => setAlwaysRefresh(e.target.checked)} />
                <span>Always refresh when checking bins</span>
              </label>
              <label className="mb-3 flex items-center gap-2 text-sm">
                <input type="checkbox" checked={dark} onChange={() => setDark((d) => !d)} />
                <span>Dark mode</span>
              </label>
              <div className="mt-4 flex items-center justify-between gap-2">
                <button onClick={() => { clearSelection(); setShowSettings(false) }} className="rounded-lg bg-red-600 px-3 py-2 text-sm font-medium text-white shadow hover:bg-red-700">Clear saved address</button>
                <button onClick={() => setShowSettings(false)} className="rounded-lg bg-gray-800 px-3 py-2 text-sm font-medium text-white shadow hover:bg-black">Close</button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
