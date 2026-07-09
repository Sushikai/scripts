import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuthStore, useNodeStore, useProxyStore } from '../store'
import { 
  Server, 
  Key, 
  TrendingUp, 
  Activity,
  Globe,
  Clock,
  Copy,
  Check,
  Download,
  Shield,
  Zap
} from 'lucide-react'

export default function DashboardPage() {
  const { user } = useAuthStore()
  const { nodes, fetchNodes } = useNodeStore()
  const { proxies, fetchProxies, fetchSubscription } = useProxyStore()
  const navigate = useNavigate()
  const [stats, setStats] = useState({
    totalNodes: 0,
    onlineNodes: 0,
    totalProxies: 0,
    activeProxies: 0,
  })
  const [copied, setCopied] = useState('')

  useEffect(() => {
    fetchNodes()
    fetchProxies()
    fetchSubscription()
  }, [])

  useEffect(() => {
    setStats({
      totalNodes: nodes.length,
      onlineNodes: nodes.filter(n => n.is_active).length,
      totalProxies: proxies.length,
      activeProxies: proxies.filter(p => p.enable).length,
    })
  }, [nodes, proxies])

  const copyToClipboard = (text) => {
    navigator.clipboard.writeText(text)
    setCopied(text)
    setTimeout(() => setCopied(''), 2000)
  }

  const statCards = [
    { label: '可用节点', value: stats.onlineNodes, total: stats.totalNodes, icon: Server, color: 'from-primary-500 to-cyan-500' },
    { label: '我的代理', value: stats.activeProxies, total: stats.totalProxies, icon: Key, color: 'from-purple-500 to-pink-500' },
    { label: '在线节点', value: stats.onlineNodes, icon: Activity, color: 'from-green-500 to-emerald-500' },
    { label: '活跃代理', value: stats.activeProxies, icon: Zap, color: 'from-orange-500 to-amber-500' },
  ]

  return (
    <div className="space-y-6">
      {/* Welcome */}
      <div className="glass-card p-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold mb-1">欢迎回来, {user?.username} 👋</h1>
            <p className="text-slate-400">这里是您的代理控制台，随时随地管理您的节点和代理</p>
          </div>
          <div className="hidden sm:flex items-center gap-2 px-4 py-2 rounded-full bg-green-500/10 text-green-400">
            <div className="w-2 h-2 rounded-full bg-green-400 animate-pulse" />
            系统运行正常
          </div>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {statCards.map(({ label, value, total, icon: Icon, color }) => (
          <div key={label} className="glass-card p-5">
            <div className="flex items-start justify-between">
              <div>
                <p className="text-slate-400 text-sm mb-1">{label}</p>
                <p className="text-3xl font-bold">
                  {value}
                  {total !== undefined && (
                    <span className="text-lg text-slate-500">/{total}</span>
                  )}
                </p>
              </div>
              <div className={`p-3 rounded-xl bg-gradient-to-br ${color}`}>
                <Icon className="w-5 h-5 text-white" />
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Quick actions */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Subscription */}
        <div className="glass-card p-6">
          <div className="flex items-center gap-2 mb-4 text-primary-400">
            <Download className="w-5 h-5" />
            <h2 className="font-semibold">订阅地址</h2>
          </div>
          <p className="text-slate-400 text-sm mb-4">
            一键订阅所有可用的代理节点，自动同步更新
          </p>
          <div className="flex gap-3">
            <input
              type="text"
              readOnly
              value={`${window.location.origin}/api/users/subscribe`}
              className="input-cyber flex-1 text-sm"
            />
            <button
              onClick={() => copyToClipboard(`${window.location.origin}/api/users/subscribe`)}
              className="btn-cyber px-4 flex items-center gap-2"
            >
              {copied ? <Check className="w-4 h-4" /> : <Copy className="w-4 h-4" />}
              复制
            </button>
          </div>
          <p className="text-xs text-slate-500 mt-3">
            ⚠️ 请妥善保管订阅链接，定期更新以获取最新节点
          </p>
        </div>

        {/* Quick guide */}
        <div className="glass-card p-6">
          <div className="flex items-center gap-2 mb-4 text-purple-400">
            <Globe className="w-5 h-5" />
            <h2 className="font-semibold">快速上手</h2>
          </div>
          <div className="space-y-3">
            {[
              { step: '1', title: '订阅节点', desc: '复制上方订阅地址，添加到您的客户端' },
              { step: '2', title: '创建代理', desc: '在"我的代理"中选择节点创建专属链接' },
              { step: '3', title: '开始使用', desc: '导入客户端，一键连接全球节点' },
            ].map(({ step, title, desc }) => (
              <div key={step} className="flex items-start gap-3 p-3 rounded-lg bg-white/5">
                <div className="w-6 h-6 rounded-full bg-purple-500/20 text-purple-400 text-sm font-bold flex items-center justify-center flex-shrink-0">
                  {step}
                </div>
                <div>
                  <p className="font-medium text-sm">{title}</p>
                  <p className="text-xs text-slate-400">{desc}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Recent nodes */}
      <div className="glass-card p-6">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2 text-primary-400">
            <Server className="w-5 h-5" />
            <h2 className="font-semibold">可用节点</h2>
          </div>
          <button onClick={() => navigate('/nodes')} className="text-sm text-primary-400 hover:text-primary-300">
            查看全部 →
          </button>
        </div>
        {nodes.length === 0 ? (
          <div className="text-center py-8 text-slate-400">
            <Server className="w-12 h-12 mx-auto mb-3 opacity-50" />
            <p>暂无可用节点</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {nodes.slice(0, 6).map((node) => (
              <div key={node.id} className="p-4 rounded-xl bg-white/5 border border-white/5 hover:border-primary-500/30 transition-colors">
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <span className="text-lg">{getCountryFlag(node.country)}</span>
                    <span className="font-medium text-sm">{node.name}</span>
                  </div>
                  <span className={`badge ${node.is_active ? 'badge-success' : 'badge-error'}`}>
                    {node.is_active ? '在线' : '离线'}
                  </span>
                </div>
                <div className="flex items-center gap-4 text-xs text-slate-400">
                  <span className="px-2 py-0.5 rounded bg-white/10 uppercase">{node.protocol}</span>
                  <span>{node.host}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function getCountryFlag(country) {
  const flags = { US: '🇺🇸', HK: '🇭🇰', JP: '🇯🇵', SG: '🇸🇬', UK: '🇬🇧', DE: '🇩🇪', KR: '🇰🇷', TW: '🇹🇼', CA: '🇨🇦', AU: '🇦🇺' }
  return flags[country] || '🌐'
}