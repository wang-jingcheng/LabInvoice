# 部署指南

[README 的快速启动](../README.md#快速启动)适合本机体验。服务器部署使用 WSGI 服务与 HTTPS 反向代理。以下以 Linux 和 Gunicorn 为例。

## 安装与运行

在代码目录创建虚拟环境，安装依赖及 WSGI 服务。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install gunicorn
```

在服务管理器或部署平台中设置环境变量。将 `LABINVOICE_DATA_DIR` 指向有读写权限的持久化目录，将 `LABINVOICE_ADMIN_PASSWORD` 设为该实例的管理员密码。多个 worker 使用同一个数据目录。会话密钥默认在该数据库中持久化，显式设置 `LABINVOICE_SECRET_KEY` 时，各 worker 使用相同的值。

在代码目录启动服务，将反向代理指向 `127.0.0.1:8000`，并在代理上配置 HTTPS。

```bash
gunicorn --bind 127.0.0.1:8000 --workers 2 --threads 2 app:app
```

如果希望完整支持单次 128 MB 的应用上传上限，请同步设置代理的请求体上限。数据目录只供应用进程读写，代理转发对资料的访问由应用鉴权处理。

已有 Gunicorn 或 uWSGI 配置可继续使用，将环境变量注入现有服务即可。 [Flask 的 Gunicorn 部署文档](https://flask.palletsprojects.com/en/stable/deploying/gunicorn/)

## 持久化资料

| 路径 | 内容 |
| --- | --- |
| `labinvoice.db` | 账号、任务、报销与归类记录、站内使用说明及应用设置 |
| `uploads/` | 主票、附件、每批次的 `meta.json`，以及说明页图片 |
| `.trash/` | 删除任务或草稿时留存的原件与归档记录 |
| `.backups/` | 自动生成的迁移前数据库副本 |
| `.cache/` | 可重新生成的 PDF 金额解析缓存 |

备份通常需要在暂停服务后同时保存 SQLite 数据库、上传目录和已有的归档目录。备份、恢复和升级的步骤见 [升级与回滚](UPGRADE.md)。
