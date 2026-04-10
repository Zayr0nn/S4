import os
from flask import Flask, render_template, request, redirect, url_for, flash, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from sqlalchemy import func
from werkzeug.security import generate_password_hash, check_password_hash

# --- CONFIGURAÇÕES ---
base_dir = os.path.abspath(os.path.dirname(__file__))

app = Flask(__name__)
app.config['SECRET_KEY'] = 'saleshub_2026_secure_key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(base_dir, 'feira.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

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
    
    produtos = db.relationship('Produto', backref='dono', lazy=True, cascade="all, delete-orphan")
    pedidos_recebidos = db.relationship('Pedido', backref='vendedor', foreign_keys='Pedido.vendedor_id', lazy=True)

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

@login_manager.user_loader
def load_user(user_id):
    return Usuario.query.get(int(user_id))

# --- ROTAS DE GERENCIAMENTO DE PRODUTOS ---

@app.route("/meus_produtos", methods=["GET", "POST"])
@login_required
def meus_produtos():
    if request.method == "POST":
        nome = request.form.get("nome")
        preco = request.form.get("preco")
        descricao = request.form.get("descricao")
        if nome and preco:
            novo_p = Produto(nome=nome, preco=float(preco), descricao=descricao, usuario_id=current_user.id)
            db.session.add(novo_p)
            db.session.commit()
            flash("Produto adicionado com sucesso!")
            return redirect(url_for('meus_produtos'))
    produtos = Produto.query.filter_by(usuario_id=current_user.id).all()
    return render_template("meus_produtos.html", produtos=produtos)

@app.route("/deletar-produto/<int:id>")
@login_required
def deletar_produto(id):
    produto = Produto.query.get_or_404(id)
    if produto.usuario_id == current_user.id or current_user.is_admin:
        db.session.delete(produto)
        db.session.commit()
        flash("Produto removido!")
    else:
        flash("Acesso negado.")
    return redirect(url_for('meus_produtos'))

# --- ROTAS DE ADMINISTRAÇÃO (ATUALIZADAS) ---

@app.route('/admin')
@login_required
def admin_panel():
    if not current_user.is_admin:
        flash("Acesso restrito aos administradores.")
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

    return render_template('admin.html', 
                           total_usuarios=total_usuarios, 
                           soma_vendas=soma_vendas,
                           vendas_por_barraca=vendas_por_barraca,
                           todos_usuarios=todos_usuarios,
                           todos_pedidos=todos_pedidos)

@app.route('/admin/toggle_admin/<int:id>')
@login_required
def toggle_admin(id):
    if not current_user.is_admin: abort(403)
    u = Usuario.query.get_or_404(id)
    if u.id == current_user.id:
        flash("❌ Você não pode revogar seu próprio acesso!")
    else:
        u.is_admin = not u.is_admin
        db.session.commit()
        status = "promovido" if u.is_admin else "rebaixado"
        flash(f"Usuário {u.username} foi {status}!")
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
        flash(f"✅ Senha de {u.username} alterada com sucesso!")
    return redirect(url_for('admin_panel'))

@app.route('/admin/delete_user/<int:id>')
@login_required
def delete_user(id):
    if not current_user.is_admin: abort(403)
    u = Usuario.query.get_or_404(id)
    if u.id == current_user.id:
        flash("❌ Erro: Você não pode deletar a si mesmo.")
    else:
        db.session.delete(u)
        db.session.commit()
        flash(f"Usuário {u.username} excluído permanentemente.")
    return redirect(url_for('admin_panel'))

@app.route('/admin/reset-database', methods=['POST'])
@login_required
def reset_database():
    if not current_user.is_admin: abort(403)
    db.drop_all()
    db.create_all()
    criar_admin_master()
    flash("✅ Banco de Dados Resetado com Sucesso!")
    return redirect(url_for('admin_panel'))

# --- RESTANTE DAS ROTAS (ESTRUTURA MANTIDA) ---

@app.route("/")
def index():
    ranking = db.session.query(
        Usuario.nome_barraca,
        Usuario.turma,
        func.sum(Pedido.valor_total).label('total')
    ).join(Pedido, Usuario.id == Pedido.vendedor_id).filter(Pedido.status == 'Confirmado')\
     .group_by(Usuario.id).order_by(func.sum(Pedido.valor_total).desc()).limit(5).all()
    barracas = Usuario.query.filter_by(tipo='vendedor').all()
    return render_template("index.html", barracas=barracas, ranking=ranking)

@app.route("/barraca/<int:usuario_id>", methods=["GET", "POST"])
@login_required
def ver_barraca(usuario_id):
    barraca = Usuario.query.get_or_404(usuario_id)
    produtos = Produto.query.filter_by(usuario_id=usuario_id).all()
    if request.method == "POST":
        total_pedido = 0
        itens_selecionados = []
        for p in produtos:
            qtd_str = request.form.get(f"qtd_{p.id}", "0")
            qtd = int(qtd_str) if qtd_str.isdigit() else 0
            if qtd > 0:
                total_pedido += (p.preco * qtd)
                itens_selecionados.append({'p': p, 'qtd': qtd})
        if total_pedido > 0:
            novo_pedido = Pedido(valor_total=total_pedido, cliente_id=current_user.id, vendedor_id=barraca.id)
            db.session.add(novo_pedido)
            db.session.flush() 
            for item in itens_selecionados:
                ip = ItemPedido(pedido_id=novo_pedido.id, produto_nome=item['p'].nome, 
                                 quantidade=item['qtd'], preco_unitario=item['p'].preco)
                db.session.add(ip)
            db.session.commit()
            return render_template("pagamento_pix.html", pedido=novo_pedido, barraca=barraca)
        flash("Selecione a quantidade de pelo menos um produto!")
    return render_template("ver_barraca.html", barraca=barraca, produtos=produtos)

@app.route("/dashboard")
@login_required
def dashboard():
    pendentes = Pedido.query.filter_by(vendedor_id=current_user.id, status='Pendente').order_by(Pedido.data_hora.desc()).all()
    confirmados = Pedido.query.filter_by(vendedor_id=current_user.id, status='Confirmado').order_by(Pedido.data_hora.desc()).all()
    total_ganho = sum(p.valor_total for p in confirmados)
    media_valor = total_ganho / len(confirmados) if len(confirmados) > 0 else 0
    return render_template("dashboard.html", pendentes=pendentes, confirmadas=confirmados, total=total_ganho, media=media_valor)

@app.route("/confirmar_pedido/<int:id>")
@login_required
def confirmar_pedido(id):
    pedido = Pedido.query.get_or_404(id)
    if pedido.vendedor_id == current_user.id or current_user.is_admin:
        pedido.status = 'Confirmado'
        db.session.commit()
        flash("Pagamento Confirmado!")
    return redirect(url_for('dashboard'))

@app.route("/cadastro", methods=["GET", "POST"])
def cadastro():
    if request.method == "POST":
        username = request.form.get("username")
        if Usuario.query.filter_by(username=username).first():
            flash("Este nome de usuário já existe.")
            return redirect(url_for("cadastro"))
        senha_hash = generate_password_hash(request.form.get("senha"))
        tipo = request.form.get("tipo")
        if tipo == "vendedor":
            novo = Usuario(username=username, senha=senha_hash, tipo="vendedor",
                           nome_barraca=request.form.get("nome"), pix=request.form.get("pix"),
                           turma=request.form.get("turma"), professor_responsavel=request.form.get("professor"),
                           ip_registro=request.remote_addr, dispositivo=request.headers.get('User-Agent'))
        else:
            novo = Usuario(username=username, senha=senha_hash, tipo="cliente",
                           ip_registro=request.remote_addr, dispositivo=request.headers.get('User-Agent'))
        db.session.add(novo)
        db.session.commit()
        flash("Conta criada com sucesso!")
        return redirect(url_for("login"))
    return render_template("cadastro.html", professores=PROFESSORES_AUTORIZADOS)

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = Usuario.query.filter_by(username=request.form.get("username")).first()
        if user and check_password_hash(user.senha, request.form.get("senha")):
            login_user(user)
            if user.is_admin: return redirect(url_for("admin_panel"))
            return redirect(url_for("dashboard") if user.tipo == 'vendedor' else url_for("index"))
        flash("Usuário ou senha incorretos.")
    return render_template("login.html")

@app.route("/logout")
def logout():
    logout_user()
    return redirect(url_for("index"))

def criar_admin_master():
    admin = Usuario.query.filter_by(username="Arthur").first()
    if not admin:
        senha_hash = generate_password_hash("zayron")
        novo_admin = Usuario(
            username="Arthur", senha=senha_hash, tipo="vendedor", is_admin=True,
            nome_barraca="Administração Central", turma="TI"
        )
        db.session.add(novo_admin)
        db.session.commit()
        print("✅ Admin Arthur criado!")

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        criar_admin_master()
    app.run(debug=True, host="0.0.0.0", port=5000)
