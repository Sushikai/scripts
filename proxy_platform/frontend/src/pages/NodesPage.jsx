import { useEffect, useState } from 'react'
import { useNodeStore, useAuthStore } from '../store'
import { Plus, Edit2, Trash2, RefreshCw, Server, Globe } from 'lucide-react'
import clsx from 'clsx'

const PROTOCOLS = ['vmess', 'vless', 'trojan', 'shadowsocks']
const NETWORKS = ['tcp', 'ws', 'grpc']
const COUNTRIES = [
  { code: 'US', name: '美国', flag: '🇺🇸' },
  { code: 'HK', name: '香港', flag: '🇭🇰' },
  { code: 'JP', name: '日本', flag: '🇯🇵' },
  { code: 'SG', name: '新加坡', flag: '🇸🇬' },
  { code: 'UK', name: '英国', flag: '🇬🇧' },
  { code: 'DE', name: '德国', flag: '🇩🇪' },
  { code: 'KR', name: '韩国', flag: '🇰🇷' },
  { code: 'TW', name: '台湾', flag: '🇹🇼' },
]

export default function NodesPage() {
  const { nodes, fetchNodes, addNode, updateNode, deleteNode, loading } = useNodeStore()
  const { user } = useAuthStore()
  const [showModal, setShowModal] = useState(false)
  const [editNode, setEditNode] = useState(null)
  const [formData, setFormData] = useState({
    name: '', host: '', port: 443, protocol: 'vmess', uuid: '', alter_id: 64,
    network: 'tcp', path: '/', tls: true, country: 'US', speed_limit: 0, data_limit: 0, is_free: false
  })

  useEffect(() => { fetchNodes() }, [])

  const openCreate = () => {
    setEditNode(null)
    setFormData({ name: '', host: '', port: 443, protocol: 'vmess', uuid: generateUUID(), alter_id: 64, network: 'tcp', path: '/', tls: true, country: 'US', speed_limit: 0, data_limit: 0, is_free: false })
    setShowModal(true)
  }

  const openEdit = (node) => {
    setEditNode(node)
    setFormData({
      name: node.name, host: node.host, port: node.port, protocol: node.protocol,
      uuid: node.uuid, alter_id: node.alter_id, network: node.network, path: node.path,
      tls: node.tls, country: node.country, speed_limit: node.speed_limit || 0,
      data_limit: node.data_limit || 0, is_free: node.is_free
    })
    setShowModal(true)
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    try {
      if (editNode) {
        await updateNode(editNode.id, formData)
      } else {
        await addNode(formData)
      }
      setShowModal(false)
      fetchNodes()
    } catch (err) {
      alert('操作失败: ' + (err.response?.data?.detail || err.message))
    }
  }

  const handleDelete = async (node) => {
    if (!confirm(`确定删除节点 "${node.name}" 吗？`)) return
    try {
      await deleteNode(node.id)
    } catch (err) {
      alert('删除失败')
    }
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">节点管理</h1>
          <p className="text-slate-400 text-sm mt-1">管理所有代理节点，添加、编辑、删除节点配置</p>
        </div>
        <div className="flex gap-3">
          <button onClick={() => fetchNodes()} className="btn-cyber flex items-center gap-2 bg-dark-200">
            <RefreshCw className={clsx('w-4 h-4', loading && 'animate-spin')} />
            刷新
          </button>
          {user?.is_admin && (
            <button onClick={openCreate} className="btn-cyber flex items-center gap-2">
              <Plus className="w-4 h-4" />
              添加节点
            </button>
          )}
        </div>
      </div>

      {/* Nodes grid */}
      {nodes.length === 0 ? (
        <div className="glass-card p-12 text-center">
          <Server className="w-16 h-16 mx-auto mb-4 text-slate-600" />
          <h3 className="text-xl font-semibold mb-2">暂无节点</h3>
          <p className="text-slate-400 mb-4">还没有添加任何节点，请先添加节点</p>
          {user?.is_admin && (
            <button onClick={openCreate} className="btn-cyber inline-flex items-center gap-2">
              <Plus className="w-4 h-4" />
              添加第一个节点
            </button>
          )}
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-4">
          {nodes.map((node) => (
            <div key={node.id} className="glass-card p-5">
              <div className="flex items-start justify-between mb-3">
                <div className="flex items-center gap-3">
                  <span className="text-2xl">{COUNTRIES.find(c => c.code === node.country)?.flag || '🌐'}</span>
                  <div>
                    <h3 className="font-semibold">{node.name}</h3>
                    <p className="text-xs text-slate-400">{node.host}:{node.port}</p>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <span className={clsx('badge', node.is_active ? 'badge-success' : 'badge-error')}>
                    {node.is_active ? '在线' : '离线'}
                  </span>
                  {node.is_free && <span className="badge badge-warning">免费</span>}
                </div>
              </div>

              <div className="flex flex-wrap gap-2 mb-4">
                <span className="px-2 py-1 rounded bg-white/10 text-xs uppercase font-mono">{node.protocol}</span>
                <span className="px-2 py-1 rounded bg-white/10 text-xs">{node.network}</span>
                {node.tls && <span className="px-2 py-1 rounded bg-white/10 text-xs text-green-400">TLS</span>}
              </div>

              {node.data_limit > 0 && (
                <div className="mb-3">
                  <div className="flex justify-between text-xs text-slate-400 mb-1">
                    <span>流量</span>
                    <span>{(node.used_data || 0).toFixed(1)} / {node.data_limit} GB</span>
                  </div>
                  <div className="h-1.5 bg-white/10 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-gradient-to-r from-primary-500 to-cyan-500 rounded-full"
                      style={{ width: `${Math.min((node.used_data / node.data_limit) * 100, 100)}%` }}
                    />
                  </div>
                </div>
              )}

              {user?.is_admin && (
                <div className="flex gap-2 pt-3 border-t border-white/5">
                  <button onClick={() => openEdit(node)} className="flex-1 py-2 rounded-lg bg-white/5 hover:bg-white/10 text-sm transition-colors flex items-center justify-center gap-1">
                    <Edit2 className="w-3.5 h-3.5" /> 编辑
                  </button>
                  <button onClick={() => handleDelete(node)} className="flex-1 py-2 rounded-lg bg-red-500/10 hover:bg-red-500/20 text-red-400 text-sm transition-colors flex items-center justify-center gap-1">
                    <Trash2 className="w-3.5 h-3.5" /> 删除
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Modal */}
      {showModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm">
          <div className="glass-card w-full max-w-lg max-h-[90vh] overflow-y-auto">
            <div className="p-6 border-b border-white/10">
              <h2 className="text-xl font-bold">{editNode ? '编辑节点' : '添加节点'}</h2>
            </div>
            <form onSubmit={handleSubmit} className="p-6 space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm text-slate-400 mb-1">节点名称</label>
                  <input value={formData.name} onChange={e => setFormData({...formData, name: e.target.value})} className="input-cyber" placeholder="美国节点-01" required />
                </div>
                <div>
                  <label className="block text-sm text-slate-400 mb-1">国家/地区</label>
                  <select value={formData.country} onChange={e => setFormData({...formData, country: e.target.value})} className="input-cyber">
                    {COUNTRIES.map(c => <option key={c.code} value={c.code}>{c.flag} {c.name}</option>)}
                  </select>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm text-slate-400 mb-1">主机地址</label>
                  <input value={formData.host} onChange={e => setFormData({...formData, host: e.target.value})} className="input-cyber" placeholder="example.com" required />
                </div>
                <div>
                  <label className="block text-sm text-slate-400 mb-1">端口</label>
                  <input type="number" value={formData.port} onChange={e => setFormData({...formData, port: parseInt(e.target.value)})} className="input-cyber" required />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm text-slate-400 mb-1">协议</label>
                  <select value={formData.protocol} onChange={e => setFormData({...formData, protocol: e.target.value})} className="input-cyber">
                    {PROTOCOLS.map(p => <option key={p} value={p}>{p.toUpperCase()}</option>)}
                  </select>
                </div>
                <div>
                  <label className="block text-sm text-slate-400 mb-1">传输协议</label>
                  <select value={formData.network} onChange={e => setFormData({...formData, network: e.target.value})} className="input-cyber">
                    {NETWORKS.map(n => <option key={n} value={n}>{n.toUpperCase()}</option>)}
                  </select>
                </div>
              </div>

              <div>
                <label className="block text-sm text-slate-400 mb-1">UUID</label>
                <input value={formData.uuid} onChange={e => setFormData({...formData, uuid: e.target.value})} className="input-cyber font-mono text-sm" placeholder="生成UUID" required />
              </div>

              <div>
                <label className="block text-sm text-slate-400 mb-1">路径 (Path)</label>
                <input value={formData.path} onChange={e => setFormData({...formData, path: e.target.value})} className="input-cyber" placeholder="/" />
              </div>

              <div className="flex items-center gap-4">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" checked={formData.tls} onChange={e => setFormData({...formData, tls: e.target.checked})} className="w-4 h-4 rounded" />
                  <span className="text-sm">启用 TLS</span>
                </label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" checked={formData.is_free} onChange={e => setFormData({...formData, is_free: e.target.checked})} className="w-4 h-4 rounded" />
                  <span className="text-sm">免费节点</span>
                </label>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm text-slate-400 mb-1">限速 (MB/s, 0=不限)</label>
                  <input type="number" value={formData.speed_limit} onChange={e => setFormData({...formData, speed_limit: parseInt(e.target.value) || 0})} className="input-cyber" />
                </div>
                <div>
                  <label className="block text-sm text-slate-400 mb-1">流量限额 (GB, 0=不限)</label>
                  <input type="number" value={formData.data_limit} onChange={e => setFormData({...formData, data_limit: parseFloat(e.target.value) || 0})} className="input-cyber" />
                </div>
              </div>

              <div className="flex gap-3 pt-4">
                <button type="button" onClick={() => setShowModal(false)} className="flex-1 py-3 rounded-xl bg-white/5 hover:bg-white/10 transition-colors">取消</button>
                <button type="submit" className="flex-1 btn-cyber">{editNode ? '保存修改' : '添加节点'}</button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}

function generateUUID() {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = Math.random() * 16 | 0
    return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16)
  })
}