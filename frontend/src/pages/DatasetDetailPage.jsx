import { useParams, useNavigate } from 'react-router-dom'
import { useState, useEffect } from 'react'
import { motion } from 'framer-motion'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer
} from 'recharts'
import { Table, FileText, Brain, RefreshCw, Loader, AlertCircle, Sparkles } from 'lucide-react'
import { datasetAPI, multimodalAPI } from '../services/api'
import toast from 'react-hot-toast'

const formatSize = (bytes) => {
  if (!bytes) return '0 KB'
  if (bytes > 1e9) return `${(bytes / 1e9).toFixed(1)} GB`
  if (bytes > 1e6) return `${(bytes / 1e6).toFixed(1)} MB`
  return `${(bytes / 1e3).toFixed(0)} KB`
}

const TABS = ['Overview', 'Preview', 'EDA', 'AI Summary']

export default function DatasetDetailPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [tab, setTab] = useState('Overview')
  const [dataset, setDataset] = useState(null)
  const [eda, setEda] = useState(null)
  const [preview, setPreview] = useState(null)
  const [loading, setLoading] = useState(true)
  const [reprocessing, setReprocessing] = useState(false)
  const [error, setError] = useState(null)
  
  // AI summary state
  const [aiSummary, setAiSummary] = useState('')
  const [generatingSummary, setGeneratingSummary] = useState(false)

  const loadData = async () => {
    try {
      setLoading(true)
      setError(null)
      const dsRes = await datasetAPI.get(id)
      setDataset(dsRes.data)

      if (dsRes.data.status === 'ready') {
        if (['csv', 'xlsx', 'xls'].includes(dsRes.data.file_type)) {
          try {
            const [edaRes, previewRes] = await Promise.all([
              datasetAPI.getEDA(id),
              datasetAPI.getPreview(id)
            ])
            setEda(edaRes.data)
            setPreview(previewRes.data)
          } catch (err) {
            console.error('Failed to load EDA or Preview data', err)
          }
        }
      }
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to load dataset details')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadData()
  }, [id])

  const handleReprocess = async () => {
    try {
      setReprocessing(true)
      await datasetAPI.process(id, {})
      toast.success('Reprocessing scheduled')
      // Poll dataset until it is ready
      let attempts = 0
      const interval = setInterval(async () => {
        attempts++
        const res = await datasetAPI.get(id)
        if (res.data.status === 'ready' || res.data.status === 'error' || attempts > 10) {
          clearInterval(interval)
          setReprocessing(false)
          loadData()
        }
      }, 2000)
    } catch (err) {
      toast.error('Failed to reprocess')
      setReprocessing(false)
    }
  }

  const handleGenerateAISummary = async () => {
    if (!dataset) return
    setGeneratingSummary(true)
    try {
      const summaryPrompt = `Analyze the dataset named "${dataset.name}".
File type: ${dataset.file_type}
Size: ${formatSize(dataset.size_bytes)}
Rows: ${dataset.rows || 'Unknown'}
Columns: ${dataset.columns?.join(', ') || 'None'}
Status: ${dataset.status}
${eda ? `EDA summary: missing values: ${eda.missing_values}, duplicates: ${eda.duplicates}` : ''}

Provide a concise, professional analysis and recommend actions.`
      
      const res = await multimodalAPI.summarize({ text: summaryPrompt, max_length: 200, style: "concise" })
      setAiSummary(res.data.summary)
    } catch (err) {
      toast.error('Failed to generate AI Summary')
    } finally {
      setGeneratingSummary(false)
    }
  }

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[400px] gap-2">
        <Loader size={32} className="animate-spin" style={{ color: 'var(--accent-primary)' }} />
        <p className="text-sm" style={{ color: 'var(--text-muted)' }}>Loading dataset details...</p>
      </div>
    )
  }

  if (error || !dataset) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[400px] text-center p-6 space-y-4">
        <AlertCircle size={48} style={{ color: '#ef4444' }} />
        <div className="space-y-1">
          <h3 className="text-lg font-semibold" style={{ color: 'var(--text-primary)' }}>Failed to Load Dataset</h3>
          <p className="text-sm max-w-sm" style={{ color: 'var(--text-muted)' }}>{error || 'Dataset not found.'}</p>
        </div>
        <button onClick={loadData} className="btn-primary">Retry</button>
      </div>
    )
  }

  if (dataset.status === 'processing' || reprocessing) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[400px] text-center p-6 space-y-4">
        <Loader size={48} className="animate-spin" style={{ color: 'var(--accent-primary)' }} />
        <div className="space-y-1">
          <h3 className="text-lg font-semibold" style={{ color: 'var(--text-primary)' }}>Dataset Processing in Progress</h3>
          <p className="text-sm max-w-sm" style={{ color: 'var(--text-muted)' }}>
            We are analyzing columns, counting rows, and running calculations. This will take a moment.
          </p>
        </div>
      </div>
    )
  }

  if (dataset.status === 'error') {
    return (
      <div className="flex flex-col items-center justify-center min-h-[400px] text-center p-6 space-y-4">
        <AlertCircle size={48} style={{ color: '#ef4444' }} />
        <div className="space-y-1">
          <h3 className="text-lg font-semibold" style={{ color: 'var(--text-primary)' }}>Processing Failed</h3>
          <p className="text-sm max-w-sm" style={{ color: 'var(--text-muted)' }}>
            An error occurred while attempting to parse or process this file.
          </p>
        </div>
        <button onClick={handleReprocess} className="btn-primary">
          <RefreshCw size={12} /> Retry Reprocess
        </button>
      </div>
    )
  }

  const isTabular = ['csv', 'xlsx', 'xls'].includes(dataset.file_type)
  const missingByColData = eda?.missing_by_column
    ? Object.entries(eda.missing_by_column).map(([name, missing]) => ({ name, missing }))
    : []

  return (
    <div className="p-6 space-y-5 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl flex items-center justify-center"
            style={{ background: 'var(--accent-muted)' }}>
            <Table size={18} style={{ color: 'var(--accent-primary)' }} />
          </div>
          <div>
            <h1 className="text-lg font-bold" style={{ color: 'var(--text-primary)' }}>{dataset.name}</h1>
            <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
              {dataset.rows ? `${dataset.rows.toLocaleString()} rows` : '—'} × {dataset.cols || '—'} columns ({formatSize(dataset.size_bytes)})
            </p>
          </div>
        </div>
        <div className="flex gap-2">
          <button onClick={handleReprocess} disabled={reprocessing} className="btn-ghost text-xs py-2">
            <RefreshCw size={12} className={reprocessing ? 'animate-spin' : ''} /> Reprocess
          </button>
          {isTabular && (
            <button onClick={() => navigate('/training')} className="btn-primary text-xs py-2">
              <Brain size={12} /> Train Model
            </button>
          )}
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 p-1 rounded-lg w-fit" style={{ background: 'var(--bg-secondary)' }}>
        {TABS.map(t => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className="px-4 py-1.5 rounded-md text-sm font-medium transition-all"
            style={{
              background: tab === t ? 'var(--bg-elevated)' : 'transparent',
              color: tab === t ? 'var(--text-primary)' : 'var(--text-muted)',
            }}
          >
            {t}
          </button>
        ))}
      </div>

      {/* Tab Contents */}
      {tab === 'Overview' && (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="space-y-4">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {[
              { label: 'Total Rows', value: dataset.rows?.toLocaleString() || '—', color: '#7c3aed' },
              { label: 'Columns', value: dataset.cols || '—', color: '#06b6d4' },
              { label: 'Missing Values', value: eda?.missing_values?.toLocaleString() || '0', color: '#f59e0b' },
              { label: 'Duplicates', value: eda?.duplicates?.toLocaleString() || '0', color: '#10b981' },
            ].map(({ label, value, color }) => (
              <div key={label} className="stat-card">
                <p className="text-2xl font-bold" style={{ color }}>{value}</p>
                <p className="text-xs" style={{ color: 'var(--text-muted)' }}>{label}</p>
              </div>
            ))}
          </div>

          {!isTabular ? (
            <div className="card p-5 text-center space-y-2">
              <FileText className="mx-auto" size={32} style={{ color: 'var(--text-muted)' }} />
              <h3 className="font-semibold text-sm" style={{ color: 'var(--text-primary)' }}>Non-Tabular File</h3>
              <p className="text-xs max-w-sm mx-auto" style={{ color: 'var(--text-muted)' }}>
                This is a {dataset.file_type.toUpperCase()} file. Detailed Exploratory Data Analysis, distributions, and column statistical summaries are only supported for tabular CSV or Excel datasets.
              </p>
            </div>
          ) : eda ? (
            <div className="card p-5 space-y-3">
              <h3 className="font-semibold text-sm" style={{ color: 'var(--text-primary)' }}>Columns & Statistical Summary</h3>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr style={{ borderBottom: '1px solid var(--border)', background: 'var(--bg-tertiary)' }}>
                      <th className="text-left px-4 py-3 font-semibold" style={{ color: 'var(--text-muted)' }}>Column</th>
                      <th className="text-left px-4 py-3 font-semibold" style={{ color: 'var(--text-muted)' }}>Type</th>
                      <th className="text-left px-4 py-3 font-semibold" style={{ color: 'var(--text-muted)' }}>Nulls</th>
                      <th className="text-left px-4 py-3 font-semibold" style={{ color: 'var(--text-muted)' }}>Unique / Mean</th>
                      <th className="text-left px-4 py-3 font-semibold" style={{ color: 'var(--text-muted)' }}>Range (Min - Max)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(eda.column_stats || {}).map(([colName, stats]) => {
                      const isNumeric = stats.mean !== undefined
                      return (
                        <tr key={colName} style={{ borderBottom: '1px solid var(--border-subtle)' }}
                          onMouseEnter={(e) => e.currentTarget.style.background = 'var(--bg-tertiary)'}
                          onMouseLeave={(e) => e.currentTarget.style.background = 'transparent'}>
                          <td className="px-4 py-2.5 font-medium" style={{ color: 'var(--text-primary)' }}>{colName}</td>
                          <td className="px-4 py-2.5" style={{ color: 'var(--text-secondary)' }}>
                            <span className={`badge ${isNumeric ? 'badge-green' : 'badge-violet'} text-[10px]`}>
                              {isNumeric ? 'Numeric' : 'Categorical'}
                            </span>
                          </td>
                          <td className="px-4 py-2.5 text-xs" style={{ color: 'var(--text-secondary)' }}>{stats.nulls}</td>
                          <td className="px-4 py-2.5 text-xs" style={{ color: 'var(--text-secondary)' }}>
                            {isNumeric ? stats.mean : `${stats.unique_values} values`}
                          </td>
                          <td className="px-4 py-2.5 text-xs" style={{ color: 'var(--text-secondary)' }}>
                            {isNumeric ? `${stats.min} to ${stats.max}` : '—'}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          ) : (
            <div className="card p-5 text-center text-xs" style={{ color: 'var(--text-muted)' }}>
              No column statistical information available.
            </div>
          )}
        </motion.div>
      )}

      {tab === 'Preview' && (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
          {!preview || preview.rows?.length === 0 ? (
            <div className="card p-5 text-center text-xs" style={{ color: 'var(--text-muted)' }}>
              Preview is not available for this file type or is empty.
            </div>
          ) : (
            <div className="card overflow-hidden">
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr style={{ borderBottom: '1px solid var(--border)', background: 'var(--bg-tertiary)' }}>
                      {preview.columns?.map(k => (
                        <th key={k} className="text-left px-4 py-3 font-semibold"
                          style={{ color: 'var(--text-muted)' }}>{k}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {preview.rows?.map((row, i) => (
                      <tr key={i} style={{ borderBottom: '1px solid var(--border-subtle)' }}
                        onMouseEnter={(e) => e.currentTarget.style.background = 'var(--bg-tertiary)'}
                        onMouseLeave={(e) => e.currentTarget.style.background = 'transparent'}>
                        {preview.columns?.map((col) => (
                          <td key={col} className="px-4 py-2.5" style={{ color: 'var(--text-secondary)' }}>
                            {String(row[col])}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </motion.div>
      )}

      {tab === 'EDA' && (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="space-y-4">
          {!isTabular ? (
            <div className="card p-5 text-center text-xs" style={{ color: 'var(--text-muted)' }}>
              EDA charts are only supported for tabular CSV or Excel files.
            </div>
          ) : missingByColData.length === 0 ? (
            <div className="card p-5 text-center text-xs space-y-1">
              <p className="font-semibold" style={{ color: 'var(--text-primary)' }}>Perfect Data Quality!</p>
              <p style={{ color: 'var(--text-muted)' }}>No missing values found across any columns in this dataset.</p>
            </div>
          ) : (
            <div className="card p-5">
              <p className="font-semibold text-sm mb-4" style={{ color: 'var(--text-primary)' }}>
                Missing Values by Column
              </p>
              <ResponsiveContainer width="100%" height={200}>
                <BarChart data={missingByColData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                  <XAxis dataKey="name" tick={{ fontSize: 11, fill: 'var(--text-muted)' }} axisLine={false} />
                  <YAxis tick={{ fontSize: 11, fill: 'var(--text-muted)' }} axisLine={false} />
                  <Tooltip />
                  <Bar dataKey="missing" fill="#f59e0b" radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}

          {isTabular && eda && (
            <div className="grid grid-cols-2 gap-4">
              <div className="card p-4">
                <p className="text-sm font-semibold mb-2" style={{ color: 'var(--text-primary)' }}>Column Types</p>
                <div className="space-y-3">
                  <div>
                    <div className="flex justify-between text-xs mb-1">
                      <span style={{ color: 'var(--text-secondary)' }}>Numeric</span>
                      <span style={{ color: '#06b6d4' }}>{eda.numeric_columns?.length || 0} cols</span>
                    </div>
                    <div className="progress-bar">
                      <div className="progress-fill" style={{ width: `${((eda.numeric_columns?.length || 0) / (dataset.cols || 1)) * 100}%`, background: '#06b6d4' }} />
                    </div>
                  </div>
                  <div>
                    <div className="flex justify-between text-xs mb-1">
                      <span style={{ color: 'var(--text-secondary)' }}>Categorical</span>
                      <span style={{ color: '#7c3aed' }}>{eda.categorical_columns?.length || 0} cols</span>
                    </div>
                    <div className="progress-bar">
                      <div className="progress-fill" style={{ width: `${((eda.categorical_columns?.length || 0) / (dataset.cols || 1)) * 100}%` }} />
                    </div>
                  </div>
                </div>
              </div>
              <div className="card p-4">
                <p className="text-sm font-semibold mb-2" style={{ color: 'var(--text-primary)' }}>Data Cleanliness</p>
                <div className="space-y-2 text-xs">
                  {[
                    { label: 'Missing Values Count', value: eda.missing_values || 0, color: eda.missing_values > 0 ? '#f59e0b' : '#10b981' },
                    { label: 'Duplicate Rows Count', value: eda.duplicates || 0, color: eda.duplicates > 0 ? '#f59e0b' : '#10b981' },
                  ].map(({ label, value, color }) => (
                    <div key={label} className="flex justify-between">
                      <span style={{ color: 'var(--text-secondary)' }}>{label}</span>
                      <span className="font-semibold" style={{ color }}>{value}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
        </motion.div>
      )}

      {tab === 'AI Summary' && (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
          <div className="card p-5 space-y-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Brain size={16} style={{ color: 'var(--accent-primary)' }} />
                <p className="font-semibold text-sm" style={{ color: 'var(--text-primary)' }}>
                  AI-Generated Dataset Summary
                </p>
              </div>
              <button
                onClick={handleGenerateAISummary}
                disabled={generatingSummary}
                className="btn-primary text-xs py-1.5 px-3 flex items-center gap-1"
              >
                {generatingSummary ? (
                  <>
                    <Loader size={12} className="animate-spin" /> Analyzing...
                  </>
                ) : (
                  <>
                    <Sparkles size={12} /> {aiSummary ? 'Regenerate Summary' : 'Generate Summary'}
                  </>
                )}
              </button>
            </div>
            
            {aiSummary ? (
              <div className="text-sm leading-relaxed whitespace-pre-line border-l-2 pl-4 py-1"
                style={{ color: 'var(--text-secondary)', borderColor: 'var(--accent-primary)' }}>
                {aiSummary}
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center py-8 text-center space-y-2">
                <Sparkles size={24} style={{ color: 'var(--text-muted)' }} />
                <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
                  Click "Generate Summary" to have the AI analyze the schema, rows, and data profile.
                </p>
              </div>
            )}
          </div>
        </motion.div>
      )}
    </div>
  )
}
