import os
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from sqlalchemy import func
from werkzeug.security import generate_password_hash, check_password_hash

# Configurações de Caminho e Banco de Dados
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
    tipo = db.Column(db.String(10), nullable=False) # 'vendedor' ou 'cliente'
    nome_barraca = db.Column(db.String(100), nullable=True)
    pix = db.Column(db.String(100), nullable=True)
    turma = db.Column(db.String(50), nullable=True)
    professor_responsavel = db.Column(db.String(50), nullable=True)
    
    produtos = db.relationship('Produto', backref='dono', lazy=True)
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
    status = db.Column(db.String(20), default='Pendente') # Pendente ou Confirmado
    data_hora = db.Column(db.DateTime, default=db.func.current_timestamp())
    cliente_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)
    vendedor_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)
    itens = db.relationship('ItemPedido', backref='pedido', lazy=True)

class ItemPedido(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    pedido_id = db.Column(db.Integer, db.ForeignKey('pedido.id'), nullable=False)
    produto_nome = db.Column(db.String(100))
    quantidade = db.Column(db.Integer, nullable=False)
    preco_unitario = db.Column(db.Float, nullable=False)

@login_manager.user_loader
def load_user(user_id):
    return Usuario.query.get(int(user_id))

# --- ROTAS DE NAVEGAÇÃO E COMPRA ---

@app.route("/")
def index():
    # Ranking: Top 5 barracas com mais vendas confirmadas
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
            db.session.flush() # Gera o ID do pedido antes do commit final
            
            for item in itens_selecionados:
                ip = ItemPedido(pedido_id=novo_pedido.id, produto_nome=item['p'].nome, 
                                quantidade=item['qtd'], preco_unitario=item['p'].preco)
                db.session.add(ip)
            
            db.session.commit()
            return render_template("pagamento_pix.html", pedido=novo_pedido, barraca=barraca)
        
        flash("Selecione a quantidade de pelo menos um produto!")
            
    return render_template("ver_barraca.html", barraca=barraca, produtos=produtos)

# --- GESTÃO DO VENDEDOR ---

@app.route("/meus-produtos", methods=["GET", "POST"])
@login_required
def meus_produtos():
    if current_user.tipo != 'vendedor': return redirect(url_for('index'))
    
    if request.method == "POST":
        try:
            nome = request.form.get("nome")
            preco = float(request.form.get("preco").replace(",", "."))
            descricao = request.form.get("descricao")
            
            novo_p = Produto(nome=nome, preco=preco, descricao=descricao, usuario_id=current_user.id)
            db.session.add(novo_p)
            db.session.commit()
            flash("Produto cadastrado!")
        except:
            flash("Erro ao cadastrar. Verifique o preço.")
            
    produtos = Produto.query.filter_by(usuario_id=current_user.id).all()
    return render_template("meus_produtos.html", produtos=produtos)

@app.route("/deletar-produto/<int:id>")
@login_required
def deletar_produto(id):
    p = Produto.query.get_or_404(id)
    if p.usuario_id == current_user.id:
        db.session.delete(p)
        db.session.commit()
    return redirect(url_for('meus_produtos'))

@app.route("/dashboard")
@login_required
def dashboard():
    if current_user.tipo != 'vendedor': return redirect(url_for('index'))
    
    pendentes = Pedido.query.filter_by(vendedor_id=current_user.id, status='Pendente').order_by(Pedido.data_hora.desc()).all()
    confirmados = Pedido.query.filter_by(vendedor_id=current_user.id, status='Confirmado').order_by(Pedido.data_hora.desc()).all()
    total_ganho = sum(p.valor_total for p in confirmados)
    
    # RESOLUÇÃO DO ERRO 'media': Definindo a variável para o template
    media_valor = 0 
    
    return render_template("dashboard.html", 
                           pendentes=pendentes, 
                           confirmadas=confirmados, 
                           total=total_ganho, 
                           media=media_valor)

@app.route("/confirmar_pedido/<int:id>")
@login_required
def confirmar_pedido(id):
    pedido = Pedido.query.get_or_404(id)
    if pedido.vendedor_id == current_user.id:
        pedido.status = 'Confirmado'
        db.session.commit()
        flash("Pagamento Confirmado com Sucesso!")
    return redirect(url_for('dashboard'))

# --- AUTENTICAÇÃO ---

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
            prof = request.form.get("professor")
            if prof not in PROFESSORES_AUTORIZADOS:
                flash("Selecione um professor autorizado.")
                return redirect(url_for("cadastro"))
            
            novo = Usuario(username=username, senha=senha_hash, tipo="vendedor",
                           nome_barraca=request.form.get("nome"), pix=request.form.get("pix"),
                           turma=request.form.get("turma"), professor_responsavel=prof)
        else:
            novo = Usuario(username=username, senha=senha_hash, tipo="cliente")

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
            return redirect(url_for("dashboard") if user.tipo == 'vendedor' else url_for("index"))
        flash("Usuário ou senha incorretos.")
    return render_template("login.html")

@app.route("/logout")
def logout():
    logout_user()
    return redirect(url_for("index"))

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True, host="0.0.0.0", port=5000)