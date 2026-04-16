import os
import sys
import base64
import logging
import json
import io

try:
    import cloudinary
    import cloudinary.uploader
    CLOUDINARY_AVAILABLE = True
except ImportError:
    CLOUDINARY_AVAILABLE = False

from flask import Flask, render_template, request, redirect, url_for, flash, abort, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from sqlalchemy import func, inspect
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

# --- CONFIGURAÇÕES ---
base_dir = os.path.abspath(os.path.dirname(__file__))

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'saleshub_2026_secure_key_dev')

# --- CORREÇÃO DO DATABASE_URL APLICADA AQUI ---
database_url = os.environ.get('DATABASE_URL', 'sqlite:///' + os.path.join(base_dir, 'feira.db'))
# Corrige prefixo incompatível do Railway / Render
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)
app.config['SQLALCHEMY_DATABASE_URI'] = database_url

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

logging.basicConfig(level=logging.INFO, stream=sys.stderr)

# --- CLOUDINARY: só ativa se TODAS as variáveis estiverem presentes ---
CLOUD_NAME = os.environ.get('CLOUDINARY_CLOUD_NAME')
CLOUD_KEY = os.environ.get('CLOUDINARY_API_KEY')
CLOUD_SECRET = os.environ.get('CLOUDINARY_API_SECRET')

if CLOUDINARY_AVAILABLE and CLOUD_NAME and CLOUD_KEY and CLOUD_SECRET:
    cloudinary.config(
        cloud_name=CLOUD_NAME,
        api_key=CLOUD_KEY,
        api_secret=CLOUD_SECRET,
        secure=True
    )
    app.logger.info("✅ Cloudinary ativo.")
else:
    CLOUDINARY_AVAILABLE = False
    app.logger.warning("⚠️ Cloudinary inativo. Fotos salvas em base64 local.")

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

PROFESSORES_AUTORIZADOS = ["Brenda", "Winaiara"]

# --- MODELOS ---

class Usuario(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    senha = db.Column(db.String(255), nullable=False)
    tipo = db.Column(db.String(10), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    nome_barraca = db.Column(db.String(100), nullable=True)
    pix = db.Column(db.String(100), nullable=True)
    turma = db.Column(db.String(50), nullable=True)
    professor_responsavel = db.Column(db.String(50), nullable=True)
    ip_registro = db.Column(db.String(50), nullable=True)
    dispositivo = db.Column(db.String(255), nullable=True)
    foto_perfil = db.Column(db.Text, nullable=True)
    serie = db.Column(db.String(50), nullable=True)
    descricao = db.Column(db.Text, nullable=True)

    produtos = db.relationship('Produto', backref='dono', lazy=True, cascade="all, delete-orphan")
    pedidos_recebidos = db.relationship('Pedido', backref='vendedor', foreign_keys='Pedido.vendedor_id', lazy=True)
    associacoes = db.relationship('MembroBarraca', backref='usuario', lazy=True, foreign_keys='MembroBarraca.usuario_id')

class Produto(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    preco = db.Column(db.Float, nullable=False)
    descricao = db.Column(db.String(200))
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)

class Pedido(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    valor_total = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default='Pendente')
    data_hora = db.Column(db.DateTime, default=db.func.current_timestamp())
    cliente_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)
    vendedor_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)
    itens = db.relationship('ItemPedido', backref='pedido', lazy=True, cascade="all, delete-orphan")

class ItemPedido(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    pedido_id = db.Column(db.Integer, db.ForeignKey('pedido.id'), nullable=False)
    produto_nome = db.Column(db.String(100))
    quantidade = db.Column(db.Integer, nullable=False)
    preco_unitario = db.Column(db.Float, nullable=False)

class Avaliacao(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nota = db.Column(db.Integer, nullable=False)
    comentario = db.Column(db.Text, nullable=True)
    data_hora = db.Column(db.DateTime, default=db.func.current_timestamp())
    autor_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)
    barraca_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)

    autor = db.relationship('Usuario', foreign_keys=[autor_id], backref='avaliacoes_feitas')
    barraca = db.relationship('Usuario', foreign_keys=[barraca_id], backref='avaliacoes_recebidas')

class MembroBarraca(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)
    barraca_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)
    status = db.Column(db.String(20), default='pendente')
    data_solicitacao = db.Column(db.DateTime, default=datetime.utcnow)
    pode_criar_produto = db.Column(db.Boolean, default=False)
    pode_confirmar_pedido = db.Column(db.Boolean, default=False)
    pode_gerenciar_membros = db.Column(db.Boolean, default=False)

    barraca = db.relationship('Usuario', foreign_keys=[barraca_id], backref='membros_associados')

@login_manager.user_loader
def load_user(user_id):
    return Usuario.query.get(int(user_id))

# --- PERMISSÕES ---

def usuario_pode_criar_produto(usuario, barraca_id):
    if usuario.id == barraca_id:
        return True
    membro = MembroBarraca.query.filter_by(usuario_id=usuario.id, barraca_id=barraca_id, status='aprovado').first()
    return membro and membro.pode_criar_produto

def usuario_pode_confirmar_pedido(usuario, barraca_id):
    if usuario.id == barraca_id:
        return True
    membro = MembroBarraca.query.filter_by(usuario_id=usuario.id, barraca_id=barraca_id, status='aprovado').first()
    return membro and membro.pode_confirmar_pedido

def usuario_pode_gerenciar_membros(usuario, barraca_id):
    if usuario.id == barraca_id:
        return True
    membro = MembroBarraca.query.filter_by(usuario_id=usuario.id, barraca_id=barraca_id, status='aprovado').first()
    return membro and membro.pode_gerenciar_membros

# --- PERFIL ---
# Rota /perfil → perfil do próprio usuário logado
# Rota /perfil/<id> → perfil de qualquer usuário (somente leitura se não for o dono)

@app.route("/perfil", methods=["GET", "POST"])
@app.route("/perfil/<int:usuario_id>", methods=["GET", "POST"])
@login_required
def perfil(usuario_id=None):
    # Se não passou ID, usa o usuário logado
    if usuario_id is None:
        usuario = current_user
    else:
        usuario = Usuario.query.get_or_404(usuario_id)

    is_self = current_user.id == usuario.id

    if request.method == "POST":
        if not is_self and not current_user.is_admin:
            flash("Você não tem permissão para alterar este perfil.", "erro")
            return redirect(url_for('perfil', usuario_id=usuario.id))

        usuario.serie = request.form.get('serie', '').strip() or None
        usuario.descricao = request.form.get('descricao', '').strip() or None

        foto = request.files.get('foto_perfil')
        if foto and foto.filename:
            if CLOUDINARY_AVAILABLE:
                try:
                    resultado = cloudinary.uploader.upload(
                        foto,
                        folder="saleshub_perfis",
                        public_id=f"user_{usuario.id}",
                        overwrite=True,
                        transformation={
                            'width': 300, 'height': 300,
                            'crop': 'fill', 'gravity': 'face'
                        }
                    )
                    usuario.foto_perfil = resultado['secure_url']
                except Exception as e:
                    app.logger.error(f"Erro Cloudinary: {e}")
                    flash("Erro ao enviar foto. Tente novamente.", "erro")
                    return redirect(url_for('perfil'))
            else:
                # Fallback: salva em base64 no banco
                try:
                    dados = base64.b64encode(foto.read()).decode('utf-8')
                    usuario.foto_perfil = f"data:{foto.mimetype};base64,{dados}"
                except Exception as e:
                    app.logger.error(f"Erro base64: {e}")
                    flash("Erro ao processar foto.", "erro")
                    return redirect(url_for('perfil'))

        db.session.commit()
        flash("Perfil atualizado com sucesso!", "sucesso")
        return redirect(url_for('perfil'))

    barraca = None
    if usuario.tipo == 'vendedor':
        barraca = usuario
    else:
        associacao = MembroBarraca.query.filter_by(usuario_id=usuario.id, status='aprovado').first()
        if associacao:
            barraca = associacao.barraca

    return render_template('perfil.html', usuario=usuario, barraca=barraca, is_self=is_self)

# --- PRODUTOS ---

@app.route("/meus_produtos", methods=["GET", "POST"])
@login_required
def meus_produtos():
    if current_user.tipo == 'vendedor':
        barraca_id = current_user.id
        tem_permissao = True
    else:
        associacao = MembroBarraca.query.filter_by(usuario_id=current_user.id, status='aprovado').first()
        if not associacao:
            flash("Você não está associado a nenhuma barraca ativa.", "erro")
            return redirect(url_for('index'))
        barraca_id = associacao.barraca_id
        tem_permissao = associacao.pode_criar_produto

    if not tem_permissao:
        flash("Você não tem permissão para gerenciar produtos.", "erro")
        return redirect(url_for('dashboard'))

    if request.method == "POST":
        nome = request.form.get("nome")
        preco = request.form.get("preco")
        descricao = request.form.get("descricao")
        if nome and preco:
            novo_p = Produto(nome=nome, preco=float(preco), descricao=descricao, usuario_id=barraca_id)
            db.session.add(novo_p)
            db.session.commit()
            flash("Produto adicionado com sucesso!", "sucesso")
            return redirect(url_for('meus_produtos'))
        else:
            flash("Nome e preço são obrigatórios.", "erro")

    produtos = Produto.query.filter_by(usuario_id=barraca_id).all()
    return render_template("meus_produtos.html", produtos=produtos)

@app.route("/deletar-produto/<int:id>")
@login_required
def deletar_produto(id):
    produto = Produto.query.get_or_404(id)
    barraca_id = produto.usuario_id
    if current_user.id == barraca_id or current_user.is_admin or usuario_pode_criar_produto(current_user, barraca_id):
        db.session.delete(produto)
        db.session.commit()
        flash("Produto removido!", "sucesso")
    else:
        flash("Acesso negado.", "erro")
    return redirect(url_for('meus_produtos'))

# --- ADMINISTRAÇÃO ---

@app.route('/admin')
@login_required
def admin_panel():
    if not current_user.is_admin:
        flash("Acesso restrito aos administradores.", "erro")
        return redirect(url_for('login'))

    total_usuarios = Usuario.query.count()
    soma_vendas = db.session.query(func.sum(Pedido.valor_total)).filter(Pedido.status == 'Confirmado').scalar() or 0

    vendas_por_barraca = db.session.query(
        Usuario.nome_barraca,
        func.sum(Pedido.valor_total).label('total')
    ).join(Pedido, Usuario.id == Pedido.vendedor_id).filter(Pedido.status == 'Confirmado')\
     .group_by(Usuario.id).order_by(func.sum(Pedido.valor_total).desc()).limit(5).all()

    todos_usuarios = Usuario.query.all()
    todos_pedidos = Pedido.query.order_by(Pedido.data_hora.desc()).all()

    barracas_lideres = Usuario.query.filter_by(tipo='vendedor').all()
    membros_por_barraca = {}
    for barraca in barracas_lideres:
        membros_por_barraca[barraca.id] = MembroBarraca.query.filter_by(barraca_id=barraca.id).all()

    return render_template('admin.html',
                           total_usuarios=total_usuarios,
                           soma_vendas=soma_vendas,
                           vendas_por_barraca=vendas_por_barraca,
                           todos_usuarios=todos_usuarios,
                           todos_pedidos=todos_pedidos,
                           barracas_lideres=barracas_lideres,
                           membros_por_barraca=membros_por_barraca)

@app.route('/admin/toggle_admin/<int:id>')
@login_required
def toggle_admin(id):
    if not current_user.is_admin: abort(403)
    u = Usuario.query.get_or_404(id)
    if u.id == current_user.id:
        flash("Você não pode revogar seu próprio acesso!", "erro")
    else:
        u.is_admin = not u.is_admin
        db.session.commit()
        flash(f"Usuário {u.username} foi {'promovido' if u.is_admin else 'rebaixado'}!", "sucesso")
    return redirect(url_for('admin_panel'))

@app.route('/admin/reset_password/<int:id>', methods=['POST'])
@login_required
def reset_password(id):
    if not current_user.is_admin: abort(403)
    u = Usuario.query.get_or_404(id)
    nova_senha = request.form.get('nova_senha')
    if nova_senha:
        u.senha = generate_password_hash(nova_senha)
        db.session.commit()
        flash(f"Senha de {u.username} alterada com sucesso!", "sucesso")
    return redirect(url_for('admin_panel'))

@app.route('/admin/delete_user/<int:id>')
@login_required
def delete_user(id):
    if not current_user.is_admin: abort(403)
    u = Usuario.query.get_or_404(id)
    if u.id == current_user.id:
        flash("Você não pode deletar a si mesmo.", "erro")
    else:
        db.session.delete(u)
        db.session.commit()
        flash(f"Usuário {u.username} excluído permanentemente.", "sucesso")
    return redirect(url_for('admin_panel'))

@app.route('/admin/remover_membro/<int:membro_id>')
@login_required
def remover_membro(membro_id):
    if not current_user.is_admin: abort(403)
    membro = MembroBarraca.query.get_or_404(membro_id)
    db.session.delete(membro)
    db.session.commit()
    flash("Associação removida.", "sucesso")
    return redirect(url_for('admin_panel'))

@app.route('/admin/reset-database', methods=['POST'])
@login_required
def reset_database():
    if not current_user.is_admin: abort(403)
    db.drop_all()
    db.create_all()
    criar_admin_master()
    flash("Banco de Dados Resetado com Sucesso!", "sucesso")
    return redirect(url_for('admin_panel'))

# --- ROTAS PRINCIPAIS ---

@app.route("/")
def index():
    ranking = db.session.query(
        Usuario.id,
        Usuario.nome_barraca,
        Usuario.turma,
        func.sum(Pedido.valor_total).label('total')
    ).join(Pedido, Usuario.id == Pedido.vendedor_id).filter(Pedido.status == 'Confirmado')\
     .group_by(Usuario.id).order_by(func.sum(Pedido.valor_total).desc()).limit(5).all()

    barracas = Usuario.query.filter_by(tipo='vendedor').all()
    membros_por_barraca = {}
    for b in barracas:
        associacoes = MembroBarraca.query.filter_by(barraca_id=b.id, status='aprovado').all()
        membros_por_barraca[b.id] = {
            'lider': b,
            'membros': [a.usuario for a in associacoes]
        }

    is_membro_ativo = False
    if current_user.is_authenticated and current_user.tipo == 'cliente':
        if MembroBarraca.query.filter_by(usuario_id=current_user.id, status='aprovado').first():
            is_membro_ativo = True

    return render_template("index.html",
                           barracas=barracas,
                           ranking=ranking,
                           membros_por_barraca=membros_por_barraca,
                           is_membro_ativo=is_membro_ativo)

@app.route("/barraca/<int:usuario_id>", methods=["GET", "POST"])
@login_required
def ver_barraca(usuario_id):
    barraca = Usuario.query.get_or_404(usuario_id)
    produtos = Produto.query.filter_by(usuario_id=usuario_id).all()

    avaliacoes = Avaliacao.query.filter_by(barraca_id=usuario_id).order_by(Avaliacao.data_hora.desc()).all()
    media_nota = db.session.query(func.avg(Avaliacao.nota)).filter_by(barraca_id=usuario_id).scalar()
    media_nota = round(media_nota, 1) if media_nota else None

    if request.method == "POST":
        if 'nota' in request.form:
            try:
                nota_int = int(request.form.get('nota'))
                if nota_int < 1 or nota_int > 5:
                    flash("Nota deve ser entre 1 e 5.", "erro")
                    return redirect(url_for('ver_barraca', usuario_id=usuario_id))
            except ValueError:
                flash("Nota inválida.", "erro")
                return redirect(url_for('ver_barraca', usuario_id=usuario_id))

            if barraca.id == current_user.id:
                flash("Você não pode avaliar sua própria barraca.", "erro")
                return redirect(url_for('ver_barraca', usuario_id=usuario_id))

            if Avaliacao.query.filter_by(autor_id=current_user.id, barraca_id=usuario_id).first():
                flash("Você já avaliou esta barraca.", "erro")
                return redirect(url_for('ver_barraca', usuario_id=usuario_id))

            comentario = request.form.get('comentario', '').strip()
            db.session.add(Avaliacao(
                nota=nota_int,
                comentario=comentario or None,
                autor_id=current_user.id,
                barraca_id=barraca.id
            ))
            db.session.commit()
            flash("Avaliação enviada com sucesso!", "sucesso")
            return redirect(url_for('ver_barraca', usuario_id=usuario_id))

        total_pedido = 0
        itens_selecionados = []
        for p in produtos:
            qtd_str = request.form.get(f"qtd_{p.id}", "0")
            qtd = int(qtd_str) if qtd_str.isdigit() else 0
            if qtd > 0:
                total_pedido += p.preco * qtd
                itens_selecionados.append({'p': p, 'qtd': qtd})

        if total_pedido > 0:
            novo_pedido = Pedido(valor_total=total_pedido, cliente_id=current_user.id, vendedor_id=barraca.id)
            db.session.add(novo_pedido)
            db.session.flush()
            for item in itens_selecionados:
                db.session.add(ItemPedido(
                    pedido_id=novo_pedido.id,
                    produto_nome=item['p'].nome,
                    quantidade=item['qtd'],
                    preco_unitario=item['p'].preco
                ))
            db.session.commit()
            return render_template("pagamento_pix.html", pedido=novo_pedido, barraca=barraca)
        else:
            flash("Selecione a quantidade de pelo menos um produto!", "erro")

    return render_template("ver_barraca.html", barraca=barraca, produtos=produtos,
                           avaliacoes=avaliacoes, media_nota=media_nota)

@app.route("/deletar_avaliacao/<int:id>")
@login_required
def deletar_avaliacao(id):
    av = Avaliacao.query.get_or_404(id)
    if av.autor_id == current_user.id or current_user.is_admin:
        barraca_id = av.barraca_id
        db.session.delete(av)
        db.session.commit()
        flash("Avaliação removida.", "sucesso")
    else:
        flash("Acesso negado.", "erro")
        barraca_id = av.barraca_id
    return redirect(url_for('ver_barraca', usuario_id=barraca_id))

@app.route("/dashboard")
@login_required
def dashboard():
    if current_user.tipo == 'vendedor':
        barraca_id = current_user.id
        is_lider = True
    else:
        associacao = MembroBarraca.query.filter_by(usuario_id=current_user.id, status='aprovado').first()
        if not associacao:
            flash("Você não está associado a nenhuma barraca ou sua solicitação ainda não foi aprovada.", "erro")
            return redirect(url_for('index'))
        barraca_id = associacao.barraca_id
        is_lider = False

    barraca = Usuario.query.get(barraca_id)
    pendentes = Pedido.query.filter_by(vendedor_id=barraca_id, status='Pendente').order_by(Pedido.data_hora.desc()).all()
    for p in pendentes:
        p.pode_confirmar = usuario_pode_confirmar_pedido(current_user, barraca_id)

    confirmados = Pedido.query.filter_by(vendedor_id=barraca_id, status='Confirmado').order_by(Pedido.data_hora.desc()).all()
    total_ganho = sum(p.valor_total for p in confirmados)
    media_valor = total_ganho / len(confirmados) if confirmados else 0

    solicitacoes, membros_aprovados = [], []
    if is_lider:
        solicitacoes = MembroBarraca.query.filter_by(barraca_id=barraca_id, status='pendente').all()
        membros_aprovados = MembroBarraca.query.filter_by(barraca_id=barraca_id, status='aprovado').all()

    return render_template("dashboard.html",
                           pendentes=pendentes, confirmadas=confirmados,
                           total=total_ganho, media=media_valor,
                           barraca=barraca, is_lider=is_lider,
                           solicitacoes=solicitacoes, membros_aprovados=membros_aprovados)

@app.route("/confirmar_pedido/<int:id>")
@login_required
def confirmar_pedido(id):
    pedido = Pedido.query.get_or_404(id)
    if usuario_pode_confirmar_pedido(current_user, pedido.vendedor_id) or current_user.is_admin:
        pedido.status = 'Confirmado'
        db.session.commit()
        flash("Pagamento confirmado com sucesso!", "sucesso")
    else:
        flash("Você não tem permissão para confirmar pedidos.", "erro")
    return redirect(url_for('dashboard'))

# --- MEMBROS ---

@app.route("/gerenciar_membros", methods=["GET", "POST"])
@login_required
def gerenciar_membros():
    if current_user.tipo != 'vendedor':
        flash("Apenas líderes de barraca podem gerenciar membros.", "erro")
        return redirect(url_for('index'))

    barraca_id = current_user.id
    if request.method == "POST":
        acao = request.form.get("acao")
        membro = MembroBarraca.query.get_or_404(request.form.get("membro_id"))
        if membro.barraca_id != barraca_id:
            abort(403)

        if acao == "aprovar":
            membro.status = 'aprovado'
            db.session.commit()
            flash(f"{membro.usuario.username} aprovado com sucesso!", "sucesso")
        elif acao == "rejeitar":
            db.session.delete(membro)
            db.session.commit()
            flash("Solicitação rejeitada.", "sucesso")
        elif acao == "atualizar_permissoes":
            membro.pode_criar_produto = 'pode_criar_produto' in request.form
            membro.pode_confirmar_pedido = 'pode_confirmar_pedido' in request.form
            membro.pode_gerenciar_membros = 'pode_gerenciar_membros' in request.form
            db.session.commit()
            flash("Permissões atualizadas.", "sucesso")
        return redirect(url_for('gerenciar_membros'))

    return render_template("gerenciar_membros.html",
                           solicitacoes=MembroBarraca.query.filter_by(barraca_id=barraca_id, status='pendente').all(),
                           membros_aprovados=MembroBarraca.query.filter_by(barraca_id=barraca_id, status='aprovado').all())

# --- AUTENTICAÇÃO ---

@app.route("/cadastro", methods=["GET", "POST"])
def cadastro():
    barracas_disponiveis = Usuario.query.filter_by(tipo='vendedor').all()
    if request.method == "POST":
        username = request.form.get("username")
        if Usuario.query.filter_by(username=username).first():
            flash("Este nome de usuário já existe.", "erro")
            return redirect(url_for("cadastro"))

        senha_hash = generate_password_hash(request.form.get("senha"))
        tipo = request.form.get("tipo")
        papel = request.form.get("papel")

        if tipo == "vendedor" and papel == "lider":
            novo = Usuario(
                username=username, senha=senha_hash, tipo="vendedor",
                nome_barraca=request.form.get("nome"), pix=request.form.get("pix"),
                turma=request.form.get("turma"), professor_responsavel=request.form.get("professor"),
                ip_registro=request.remote_addr, dispositivo=request.headers.get('User-Agent')
            )
            db.session.add(novo)
            db.session.commit()
            flash("Barraca criada com sucesso! Bem-vindo ao SalesHub.", "sucesso")
            login_user(novo)
            return redirect(url_for("dashboard"))

        elif tipo == "cliente" and papel == "membro":
            barraca_id = request.form.get("barraca_id")
            if not barraca_id:
                flash("Selecione uma barraca para se associar.", "erro")
                return redirect(url_for("cadastro"))
            novo = Usuario(
                username=username, senha=senha_hash, tipo="cliente",
                ip_registro=request.remote_addr, dispositivo=request.headers.get('User-Agent')
            )
            db.session.add(novo)
            db.session.flush()
            db.session.add(MembroBarraca(usuario_id=novo.id, barraca_id=int(barraca_id), status='pendente'))
            db.session.commit()
            flash("Conta criada! Aguarde a aprovação do líder da barraca.", "sucesso")
            login_user(novo)
            return redirect(url_for("index"))

        else:
            novo = Usuario(
                username=username, senha=senha_hash, tipo="cliente",
                ip_registro=request.remote_addr, dispositivo=request.headers.get('User-Agent')
            )
            db.session.add(novo)
            db.session.commit()
            flash("Conta criada com sucesso! Bem-vindo ao SalesHub.", "sucesso")
            login_user(novo)
            return redirect(url_for("index"))

    return render_template("cadastro.html", professores=PROFESSORES_AUTORIZADOS, barracas=barracas_disponiveis)

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = Usuario.query.filter_by(username=request.form.get("username")).first()
        if user and check_password_hash(user.senha, request.form.get("senha")):
            login_user(user)
            flash(f"Bem-vindo de volta, {user.username}!", "sucesso")
            if user.is_admin:
                return redirect(url_for("admin_panel"))
            return redirect(url_for("dashboard") if user.tipo == 'vendedor' else url_for("index"))
        flash("Usuário ou senha incorretos.", "erro")
    return render_template("login.html")

@app.route("/logout")
def logout():
    logout_user()
    return redirect(url_for("index"))

# --- BACKUP ---

@app.route('/admin/criar_backup', methods=['POST'])
@login_required
def criar_backup():
    if not current_user.is_admin:
        abort(403)

    dados = {
        'backup_em': datetime.utcnow().isoformat(),
        'usuarios': [
            {
                'id': u.id, 'username': u.username, 'senha': u.senha,
                'tipo': u.tipo, 'is_admin': u.is_admin,
                'nome_barraca': u.nome_barraca, 'pix': u.pix,
                'turma': u.turma, 'professor_responsavel': u.professor_responsavel,
                'ip_registro': u.ip_registro, 'dispositivo': u.dispositivo,
                'foto_perfil': u.foto_perfil, 'serie': u.serie, 'descricao': u.descricao
            } for u in Usuario.query.all()
        ],
        'produtos': [
            {'id': p.id, 'nome': p.nome, 'preco': p.preco,
             'descricao': p.descricao, 'usuario_id': p.usuario_id}
            for p in Produto.query.all()
        ],
        'pedidos': [
            {
                'id': ped.id, 'valor_total': ped.valor_total,
                'status': ped.status, 'data_hora': ped.data_hora.isoformat() if ped.data_hora else None,
                'cliente_id': ped.cliente_id, 'vendedor_id': ped.vendedor_id,
                'itens': [
                    {'produto_nome': it.produto_nome, 'quantidade': it.quantidade,
                     'preco_unitario': it.preco_unitario}
                    for it in ped.itens
                ]
            } for ped in Pedido.query.all()
        ],
        'avaliacoes': [
            {'id': a.id, 'nota': a.nota, 'comentario': a.comentario,
             'data_hora': a.data_hora.isoformat() if a.data_hora else None,
             'autor_id': a.autor_id, 'barraca_id': a.barraca_id}
            for a in Avaliacao.query.all()
        ],
        'membros_barraca': [
            {
                'id': m.id, 'usuario_id': m.usuario_id, 'barraca_id': m.barraca_id,
                'status': m.status, 'data_solicitacao': m.data_solicitacao.isoformat() if m.data_solicitacao else None,
                'pode_criar_produto': m.pode_criar_produto,
                'pode_confirmar_pedido': m.pode_confirmar_pedido,
                'pode_gerenciar_membros': m.pode_gerenciar_membros
            } for m in MembroBarraca.query.all()
        ]
    }

    arquivo = io.BytesIO(json.dumps(dados, ensure_ascii=False, indent=2).encode('utf-8'))
    nome = f"backup_saleshub_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
    return send_file(arquivo, download_name=nome, as_attachment=True, mimetype='application/json')

@app.route('/admin/restaurar_backup', methods=['POST'])
@login_required
def restaurar_backup():
    if not current_user.is_admin:
        abort(403)

    arquivo = request.files.get('arquivo_backup')
    if not arquivo or not arquivo.filename:
        flash("Nenhum arquivo selecionado.", "erro")
        return redirect(url_for('admin_panel'))

    if not arquivo.filename.endswith('.json'):
        flash("O arquivo deve ser um .json válido.", "erro")
        return redirect(url_for('admin_panel'))

    try:
        conteudo = json.loads(arquivo.read().decode('utf-8'))
    except Exception:
        flash("Erro ao ler o arquivo JSON.", "erro")
        return redirect(url_for('admin_panel'))

    # Confirmação explícita via form
    confirmacao = request.form.get('confirmar_restauracao')
    if confirmacao != 'RESTAURAR':
        flash("Confirme a restauração marcando a caixa de confirmação.", "erro")
        return redirect(url_for('admin_panel'))

    try:
        db.session.query(ItemPedido).delete()
        db.session.query(Pedido).delete()
        db.session.query(Avaliacao).delete()
        db.session.query(MembroBarraca).delete()
        db.session.query(Produto).delete()
        db.session.query(Usuario).delete()
        db.session.commit()

        # Mapeia IDs antigos → novos IDs
        id_map = {}
        for u_data in conteudo.get('usuarios', []):
            novo = Usuario(
                id=u_data['id'], username=u_data['username'], senha=u_data['senha'],
                tipo=u_data['tipo'], is_admin=u_data['is_admin'],
                nome_barraca=u_data.get('nome_barraca'), pix=u_data.get('pix'),
                turma=u_data.get('turma'), professor_responsavel=u_data.get('professor_responsavel'),
                ip_registro=u_data.get('ip_registro'), dispositivo=u_data.get('dispositivo'),
                foto_perfil=u_data.get('foto_perfil'), serie=u_data.get('serie'),
                descricao=u_data.get('descricao')
            )
            db.session.add(novo)
            id_map[u_data['id']] = novo.id

        db.session.commit()

        for p_data in conteudo.get('produtos', []):
            db.session.add(Produto(
                nome=p_data['nome'], preco=p_data['preco'],
                descricao=p_data.get('descricao'),
                usuario_id=id_map.get(p_data['usuario_id'], p_data['usuario_id'])
            ))

        for m_data in conteudo.get('membros_barraca', []):
            db.session.add(MembroBarraca(
                usuario_id=id_map.get(m_data['usuario_id'], m_data['usuario_id']),
                barraca_id=id_map.get(m_data['barraca_id'], m_data['barraca_id']),
                status=m_data['status'],
                pode_criar_produto=m_data.get('pode_criar_produto', False),
                pode_confirmar_pedido=m_data.get('pode_confirmar_pedido', False),
                pode_gerenciar_membros=m_data.get('pode_gerenciar_membros', False)
            ))

        for a_data in conteudo.get('avaliacoes', []):
            db.session.add(Avaliacao(
                nota=a_data['nota'], comentario=a_data.get('comentario'),
                autor_id=id_map.get(a_data['autor_id'], a_data['autor_id']),
                barraca_id=id_map.get(a_data['barraca_id'], a_data['barraca_id'])
            ))

        for ped_data in conteudo.get('pedidos', []):
            novo_pedido = Pedido(
                valor_total=ped_data['valor_total'], status=ped_data['status'],
                cliente_id=id_map.get(ped_data['cliente_id'], ped_data['cliente_id']),
                vendedor_id=id_map.get(ped_data['vendedor_id'], ped_data['vendedor_id'])
            )
            db.session.add(novo_pedido)
            db.session.flush()
            for it_data in ped_data.get('itens', []):
                db.session.add(ItemPedido(
                    pedido_id=novo_pedido.id,
                    produto_nome=it_data['produto_nome'],
                    quantidade=it_data['quantidade'],
                    preco_unitario=it_data['preco_unitario']
                ))

        db.session.commit()
        flash(f"Backup restaurado com sucesso! {len(conteudo.get('usuarios', []))} usuários recuperados.", "sucesso")

    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Erro na restauração: {e}")
        flash(f"Erro ao restaurar backup: {str(e)}", "erro")

    return redirect(url_for('admin_panel'))

# --- HEALTH CHECK ---

@app.route('/health')
def health():
    return 'OK', 200

# --- INICIALIZAÇÃO ---

def criar_admin_master():
    if not Usuario.query.filter_by(username="Arthur").first():
        db.session.add(Usuario(
            username="Arthur",
            senha=generate_password_hash("zayron"),
            tipo="vendedor", is_admin=True,
            nome_barraca="Administração Central", turma="TI"
        ))
        db.session.commit()
        app.logger.info("✅ Admin Arthur criado!")

_inicializado = False

@app.before_request
def inicializar_uma_vez():
    global _inicializado
    if _inicializado:
        return
    _inicializado = True
    try:
        db.create_all()
        criar_admin_master()
        app.logger.info("✅ Worker inicializado.")
    except Exception as e:
        app.logger.error(f"❌ Erro: {e}", exc_info=True)

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
