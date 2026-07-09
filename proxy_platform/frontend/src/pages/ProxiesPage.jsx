import { useEffect, useState } from 'react'
import { useProxyStore, useNodeStore } from '../store'
import { Plus, Trash2, Key, Copy, Check, QrCode, Download, RefreshCw } from 'lucide-react'
import clsx from 'clsx'

const PROTOCOLS = [
  { value: 'vmess', label: 'VMess', color: 'from-blue-500 to-cyan-500' },
  { value: 'vless', label: 'VLESS', color: 'from-yellow-500 to-orange-500' },
  { value: 'trojan', label: 'Trojan', color: 'from-red-500 to-pink-500' },
  { value: 'shadowsocks', label: 'Shadowsocks', color: 'from-purple-500 to-indigo-500' },
]

export default function ProxiesPage() {
  const { proxies, fetchProxies, createProxy, deleteProxy, getProxyInfo, fetchSubscription, subscription } = useProxyStore()
  const { nodes, fetchNodes } = useNodeStore()
  const [showCreate, setShowCreate] = useState(false)
  const [selectedProtocol, setSelectedProtocol] = useState('vmess')
  const [selectedNode, setSelectedNode] = useState('')
  const [proxyInfo, setProxyInfo] = useState({})
  const [copied, setCopied] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    fetchProxies()
    fetchNodes()
    fetchSubscription()
  }, [])

  const copyToClipboard = (text) => {
    navigator.clipboard.writeText(text)
    setCopied(text)
    setTimeout(() => setCopied(''), 2000)
  }

  const handleCreate = async (e) => {
    e.preventDefault()
    if (!selectedNode) return
    setLoading(true)
    try {
      const proxy = await createProxy(parseInt(selectedNode), selectedProtocol)
      const info = await getProxyInfo(proxy.id)
      setProxyInfo({ ...info, proxy_id: proxy.id })
      setShowCreate(false)
      fetchProxies()
    } catch (err) {
      alert('创建失败: ' + (err.response?.data?.detail || err.message))
    } finally {
      setLoading(false)
    }
  }

  const handleDelete = async (proxy) => {
    if (!confirm('确定删除此代理？')) return
    try {
      await deleteProxy(proxy.id)
    } catch (err) {
      alert('删除失败')
    }
  }

  const getProtocolColor = (protocol) => {
    return PROTOCOLS.find(p => p.value === protocol)?.color || 'from-gray-500 to-gray-600'
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">我的代理</h1>
          <p className="text-slate-400 text-sm mt-1">创建和管理您的代理节点链接</p>
        </div>
        <div className="flex gap-3">
          <button onClick={() => fetchProxies()} className="btn-cyber flex items-center gap-2 bg-dark-200">
            <RefreshCw className="w-4 h-4" /> 刷新
          </button>
          <button onClick={() => setShowCreate(true)} className="btn-cyber flex items-center gap-2">
            <Plus className="w-4 h-4" /> 创建代理
          </button>
        </div>
      </div>

      {/* Subscription banner */}
      <div className="glass-card p-5 border-primary-500/20">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h3 className="font-semibold mb-1 flex items-center gap-2">
              <Download className="w-4 h-4 text-primary-400" />
              订阅地址
            </h3>
            <p className="text-slate-400 text-sm">使用订阅功能，一次订阅所有节点，自动同步更新</p>
            <div className="flex items-center gap-2 mt-3">
              <input
                readOnly
                value={`${window.location.origin}/api/users/subscribe`}
                className="input-cyber flex-1 text-sm max-w-md"
              />
              <button
                onClick={() => copyToClipboard(`${window.location.origin}/api/users/subscribe`)}
                className="btn-cyber px-3"
              >
                {copied ? <Check className="w-4 h-4" /> : <Copy className="w-4 h-4" />}
              </button>
            </div>
          </div>
          {subscription?.content && (
            <button
              onClick={() => copyToClipboard(atob(subscription.content))}
              className="btn-cyber flex items-center gap-2"
            >
              <Key className="w-4 h-4" /> 复制原始配置
            </button>
          )}
        </div>
      </div>

      {/* Proxies list */}
      {proxies.length === 0 ? (
        <div className="glass-card p-12 text-center">
          <Key className="w-16 h-16 mx-auto mb-4 text-slate-600" />
          <h3 className="text-xl font-semibold mb-2">暂无代理</h3>
          <p className="text-slate-400 mb-4">创建您的第一个代理，开始使用</p>
          <button onClick={() => setShowCreate(true)} className="btn-cyber inline-flex items-center gap-2">
            <Plus className="w-4 h-4" /> 创建代理
          </button>
        </div>
      ) : (
        <div className="grid gap-4">
          {proxies.map((proxy) => (
            <div key={proxy.id} className="glass-card p-5">
              <div className="flex items-start justify-between gap-4">
                <div className="flex-1">
                  <div className="flex items-center gap-3 mb-3">
                    <div className={clsx('px-3 py-1 rounded-lg bg-gradient-to-r text-white text-sm font-medium', getProtocolColor(proxy.protocol))}>
                      {proxy.protocol.toUpperCase()}
                    </div>
                    <span className={clsx('badge', proxy.enable ? 'badge-success' : 'badge-error')}>
                      {proxy.enable ? '启用' : '禁用'}
                    </span>
                    <span className="text-slate-400 text-sm">端口: {proxy.inlet_port}</span>
                  </div>
                  <div className="font-mono text-xs text-slate-400 bg-black/20 rounded p-2 mb-3">
                    UUID: {proxy.uuid}
                  </div>
                </div>
                <button onClick={() => handleDelete(proxy)} className="p-2 rounded-lg bg-red-500/10 hover:bg-red-500/20 text-red-400 transition-colors">
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Create modal */}
      {showCreate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm">
          <div className="glass-card w-full max-w-md">
            <div className="p-6 border-b border-white/10">
              <h2 className="text-xl font-bold">创建新代理</h2>
            </div>
            <form onSubmit={handleCreate} className="p-6 space-y-5">
              {/* Protocol selection */}
              <div>
                <label className="block text-sm text-slate-400 mb-2">选择协议</label>
                <div className="grid grid-cols-2 gap-2">
                  {PROTOCOLS.map(({ value, label, color }) => (
                    <button
                      key={value}
                      type="button"
                      onClick={() => setSelectedProtocol(value)}
                      className={clsx(
                        'py-3 rounded-xl border text-sm font-medium transition-all',
                        selectedProtocol === value
                          ? 'border-primary-500 bg-primary-500/10 text-primary-400'
                          : 'border-white/10 text-slate-400 hover:border-white/20'
                      )}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>

              {/* Node selection */}
              <div>
                <label className="block text-sm text-slate-400 mb-2">选择节点</label>
                <select
                  value={selectedNode}
                  onChange={e => setSelectedNode(e.target.value)}
                  className="input-cyber"
                  required
                >
                  <option value="">请选择节点</option>
                  {nodes.filter(n => n.is_active).map(node => (
                    <option key={node.id} value={node.id}>
                      {node.name} - {node.host} ({node.protocol})
                    </option>
                  ))}
                </select>
              </div>

              <div className="flex gap-3 pt-2">
                <button type="button" onClick={() => setShowCreate(false)} className="flex-1 py-3 rounded-xl bg-white/5 hover:bg-white/10 transition-colors">
                  取消
                </button>
                <button type="submit" disabled={loading || !selectedNode} className="flex-1 btn-cyber flex items-center justify-center gap-2">
                  {loading ? <><div className="spinner" /> 创建中...</> : <><Plus className="w-4 h-4" /> 创建</>}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}